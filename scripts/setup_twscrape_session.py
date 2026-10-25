"""从浏览器 Cookie 创建仅保存在本机的 twscrape 会话数据库。"""

from __future__ import annotations

import asyncio
import getpass
import os
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

import config  # noqa: E402


def _database_path() -> Path:
    path = Path(config.TWSCRAPE_DB_FILE).expanduser()
    return path if path.is_absolute() else (PROJECT_ROOT / path).resolve()


def _cookie_string(auth_token: str, ct0: str) -> str:
    auth_token = auth_token.strip()
    ct0 = ct0.strip()
    if not auth_token or not ct0:
        raise ValueError("auth_token 和 ct0 都不能为空")
    if any(character in auth_token + ct0 for character in "\r\n;"):
        raise ValueError("Cookie 值格式无效")
    return f"auth_token={auth_token}; ct0={ct0}"


async def create_session(path: Path) -> None:
    if sys.version_info < (3, 10):
        raise RuntimeError(
            "twscrape 0.20.1 需要 Python >=3.10；"
            f"当前为 {sys.version_info.major}.{sys.version_info.minor}"
        )
    try:
        from twscrape import API
    except ImportError as exc:
        raise RuntimeError(
            "缺少 twscrape，请先运行 python3 -m pip install -r requirements.txt"
        ) from exc

    username = input("X 用户名（不含 @）：").strip()
    auth_token = getpass.getpass("浏览器 Cookie auth_token（不会显示）：")
    ct0 = getpass.getpass("浏览器 Cookie ct0（不会显示）：")
    if not username:
        raise ValueError("用户名不能为空")

    path.parent.mkdir(parents=True, exist_ok=True)
    api = API(pool=str(path), raise_when_no_account=True)
    try:
        await api.pool.add_account_cookies(
            username,
            _cookie_string(auth_token, ct0),
        )
        account = await api.pool.get_account(username)
        if account is None or not account.active:
            raise RuntimeError("twscrape 没有创建可用账号会话")
    finally:
        if path.exists() and os.name != "nt":
            path.chmod(0o600)


def main() -> int:
    path = _database_path()
    if path.exists():
        print(f"会话数据库已存在：{path}")
        print("如需更换 Cookie，请先手动移走该文件，再重新运行本脚本。")
        return 2

    print("该操作只在本机保存浏览器 Cookie，不会要求或保存 X 密码。")
    print(f"会话数据库：{path}")
    print("请勿把 auth_token、ct0 或该数据库发到聊天中或提交到 GitHub。")
    try:
        asyncio.run(create_session(path))
    except (KeyboardInterrupt, EOFError):
        print("\n已取消会话创建")
        return 130
    except Exception as exc:
        print(f"会话创建失败：{type(exc).__name__}: {exc}")
        return 1

    print("twscrape 本地会话已创建。请勿提交或分享该文件。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
