# -*- coding: utf-8 -*-
"""OSS 凭证密文生成脚本（方案 A：Fernet 密文 + 环境变量密钥）。

用法:
    AGENTSCOPE_OSS_KEY=<fernet-key> python scripts/encrypt_oss_credentials.py <AccessKeyId> <AccessKeySecret>

密钥生成:
    python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

输出: 两行密文，分别对应 AccessKeyId / AccessKeySecret，
      填入 bocomadp/routers/oss_download.py 的 _OSS_ACCESS_KEY_ID_ENC /
      _OSS_ACCESS_KEY_SECRET_ENC。
"""
from __future__ import annotations

import os
import sys

from cryptography.fernet import Fernet


def main() -> int:
    key = os.environ.get("AGENTSCOPE_OSS_KEY")
    if not key:
        print(
            "ERROR: AGENTSCOPE_OSS_KEY 环境变量未设置",
            file=sys.stderr,
        )
        return 1
    if len(sys.argv) != 3:
        print(
            "USAGE: AGENTSCOPE_OSS_KEY=<key> python scripts/encrypt_oss_credentials.py "
            "<AccessKeyId> <AccessKeySecret>",
            file=sys.stderr,
        )
        return 1
    f = Fernet(key.encode())
    for plain in sys.argv[1:]:
        print(f.encrypt(plain.encode()).decode())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
