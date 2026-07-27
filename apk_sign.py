import os
import sys
import subprocess
import base64
import ecdsa
import hashlib
import datetime
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives import serialization, hashes
from cryptography.hazmat.primitives.serialization import pkcs12  # 新增：用于打包 p12
from cryptography.x509.oid import NameOID
from cryptography import x509

def generate_deterministic_keys(secexp, curve='NIST256p', comment="", out_dir='/home/vscode/.ssh/'):
    """
    根据唯一整数 secexp 确定性地生成 ECDSA 私钥和完全固定的 X.509 证书。
    并打包为 Android 签名最稳定的 PKCS#12 (Keystore) 格式。
    """
    os.makedirs(out_dir, exist_ok=True)

    if curve != "NIST256p":
        raise ValueError("APK签名仅支持 NIST256p (secp256r1)")

    if isinstance(secexp, str):
        comment = secexp if not comment else comment
        secexp = int(secexp)
    if not comment:
        comment = str(secexp)

    # ========== 1. 由secret_exponent生成ecdsa库密钥 ==========
    sk_ecdsa = ecdsa.SigningKey.from_secret_exponent(secexp=secexp, curve=ecdsa.NIST256p)
    vk_ecdsa = sk_ecdsa.verifying_key

    # ========== 2. 转换为 cryptography 标准EC私钥对象 ==========
    private_numbers = ec.EllipticCurvePrivateNumbers(
        private_value=secexp,
        public_numbers=ec.EllipticCurvePublicNumbers(
            x=vk_ecdsa.pubkey.point.x(),
            y=vk_ecdsa.pubkey.point.y(),
            curve=ec.SECP256R1()
        )
    )
    priv_key = private_numbers.private_key()

    # ========== 3. 生成绝对确定的 X.509 证书 ==========
    serial_number = int(hashlib.sha256(str(secexp).encode()).hexdigest(), 16) % (2**63 - 1)
    if serial_number == 0: 
        serial_number = 1

    not_valid_before = datetime.datetime(2024, 1, 1, 0, 0, 0, tzinfo=datetime.timezone.utc)
    not_valid_after = datetime.datetime(2099, 12, 31, 23, 59, 59, tzinfo=datetime.timezone.utc)

    subject = issuer = x509.Name([
        x509.NameAttribute(NameOID.COUNTRY_NAME, "CN"),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, "SELF"),
        x509.NameAttribute(NameOID.COMMON_NAME, "APK_SIGNER"),
    ])
    
    cert = x509.CertificateBuilder().subject_name(
        subject
    ).issuer_name(
        issuer
    ).public_key(
        priv_key.public_key()
    ).serial_number(
        serial_number
    ).not_valid_before(
        not_valid_before
    ).not_valid_after(
        not_valid_after
    ).sign(priv_key, hashes.SHA256())

    # ========== 4. 【核心修复】生成 PKCS#12 (Keystore) ==========
    # 彻底绕开 apksigner 37.0.0 针对纯 PEM 文件的解析 Bug
    p12_password = b"123456" # 给 keystore 设一个基础密码
    p12_data = pkcs12.serialize_key_and_certificates(
        name=b"apk_signer", # 这是 key alias
        key=priv_key,
        cert=cert,
        cas=None,
        encryption_algorithm=serialization.BestAvailableEncryption(p12_password)
    )
    
    p12_path = os.path.join(out_dir, f"apk_sign_keystore_{comment}.p12")
    with open(p12_path, "wb") as f:
        f.write(p12_data)

    # ========== 5. OpenSSH 格式保留 (用于兼容你之前的需求) ==========
    ssh_sk_pem = sk_ecdsa.to_pem()
    ssh_priv_path = os.path.join(out_dir, f"privateKey_{curve}_{comment}.pem")
    with open(ssh_priv_path, "wb") as f:
        f.write(ssh_sk_pem)

    prefix = b"\x00\x00\x00\x13ecdsa-sha2-nistp256\x00\x00\x00\x08nistp256\x00\x00\x00A"
    pub_raw = vk_ecdsa.to_string(encoding="uncompressed")
    pub_b64 = base64.b64encode(prefix + pub_raw).decode("utf-8")
    ssh_pub_line = f"ecdsa-sha2-nistp256 {pub_b64} #{comment}".encode()
    ssh_pub_path = os.path.join(out_dir, f"publicKey_{curve}_{comment}.pub")
    with open(ssh_pub_path, "wb") as f:
        f.write(ssh_pub_line)

    return {
        "p12_keystore_path": p12_path,
        "key_alias": "apk_signer",
        "key_password": "pass:123456"
    }


def sign_apk(apk_path, keystore_info):
    """
    使用 PKCS#12 Keystore 调用 apksigner 给指定的 APK 签名
    """
    if not os.path.exists(apk_path):
        raise FileNotFoundError(f"找不到需要签名的APK文件: {apk_path}")

    # 自动检查已安装的 apksigner 版本路径
    apksigner_paths = [
        "/home/vscode/.buildozer/android/platform/android-sdk/build-tools/37.0.0/apksigner",
        "/home/vscode/.buildozer/android/platform/android-sdk/build-tools/34.0.0/apksigner"
    ]
    
    apksigner_cmd = next((p for p in apksigner_paths if os.path.exists(p)), None)
    
    if not apksigner_cmd:
        raise FileNotFoundError("未找到 apksigner，请确保 build-tools 已安装。")

    # 构造输出的已签名 APK 文件路径
    dir_name = os.path.dirname(apk_path)
    base_name = os.path.basename(apk_path)
    if base_name.endswith("-unsigned.apk"):
        signed_apk_name = base_name.replace("-unsigned.apk", "-signed.apk")
    else:
        name, ext = os.path.splitext(base_name)
        signed_apk_name = f"{name}-signed{ext}"
        
    signed_apk_path = os.path.join(dir_name, signed_apk_name)

    # 构造基于 Keystore 签名的命令
    cmd = [
        apksigner_cmd, "sign",
        "--ks", keystore_info["p12_keystore_path"],
        "--ks-type", "pkcs12",
        "--ks-pass", keystore_info["key_password"],
        "--ks-key-alias", keystore_info["key_alias"],
        "--out", signed_apk_path,
        apk_path
    ]
    
    print("=" * 50)
    print(f"使用 Keystore: {keystore_info['p12_keystore_path']}")
    print(f"签名命令: {' '.join(cmd)}")
    print("=" * 50)
    
    try:
        subprocess.run(cmd, check=True)
        print(f"\n✅ 签名成功！\n已生成签名包: {os.path.abspath(signed_apk_path)}")
    except subprocess.CalledProcessError as e:
        print(f"\n❌ 签名过程失败: {e}")


if __name__ == "__main__":
    # ---------------- 核心配置区 ---------------- #
    # 唯一私钥指数 (必须是整数)。建议每次固定相同的数字即可重现完全一样的签名。
    UNIQUE_SECEXP = 2**64

    # 允许通过命令行参数传入 APK 路径；如果没有提供，则直接报错。
    UNSIGNED_APK = None
    if len(sys.argv) > 1:
        UNSIGNED_APK = sys.argv[1]

    if not UNSIGNED_APK:
        UNSIGNED_APK = "/workspaces/kivy/YOLOv8-Mobile/app/build/outputs/apk/release/app-release-unsigned.apk"
        #UNSIGNED_APK = '/workspaces/kivy/YOLOv8-Mobile/app/build/tmp/app-release-unsigned.apk'
        print(f"Usage: python3 apk_sign.py <unsigned_apk_path> \n use default: {UNSIGNED_APK}")
        #raise SystemExit(1)

    KEY_OUTPUT_DIR = os.path.expanduser("~") + "/.ssh/"
    # -------------------------------------------- #

    # 1. 生成确定性 Keystore 文件 (.p12)
    keystore_info = generate_deterministic_keys(
        secexp=UNIQUE_SECEXP,
        out_dir=KEY_OUTPUT_DIR
    )

    # 2. 对 APK 进行签名
    sign_apk(
        apk_path=UNSIGNED_APK,
        keystore_info=keystore_info
    )