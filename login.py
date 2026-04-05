#!/usr/bin/env python3
"""
一次性登录脚本。运行后弹出浏览器，手动登录头条号，
关闭浏览器后 cookie 自动保存到 .auth/ 目录。
"""
import os
from playwright.sync_api import sync_playwright

AUTH_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".auth")
URL = "https://mp.toutiao.com/profile_v4/activity/hot-selection"

def main():
    os.makedirs(AUTH_DIR, exist_ok=True)
    print("正在打开浏览器，请登录头条号...")
    print("登录成功后，直接关闭浏览器窗口，cookie 会自动保存。\n")

    with sync_playwright() as p:
        browser = p.chromium.launch_persistent_context(
            user_data_dir=AUTH_DIR,
            headless=False,
            args=["--window-size=1280,800"],
        )
        page = browser.new_page()
        page.goto(URL)
        print("浏览器已打开，等待你登录并关闭窗口...")
        browser.wait_for_event("close", timeout=300_000)  # 最多等5分钟
        print(f"\n✓ 登录状态已保存到 {AUTH_DIR}")
        print("以后运行 scrape.py 会自动复用此登录状态。")

if __name__ == "__main__":
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    main()
