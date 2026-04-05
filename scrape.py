#!/usr/bin/env python3
"""
抓取头条号热选榜，过滤科技/AI/教育相关话题，保存到 data/ 目录并推送 GitHub。
每2小时运行一次（由 launchd 调度）。
"""
import os
import re
import sys
from datetime import datetime
from playwright.sync_api import sync_playwright

AUTH_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".auth")
URL = "https://mp.toutiao.com/profile_v4/activity/hot-selection"

KEYWORDS = [
    # 科技
    "科技", "技术", "芯片", "半导体", "量子", "机器人", "无人机", "卫星", "火箭", "航天",
    "手机", "苹果", "华为", "小米", "特斯拉", "新能源", "电动车", "自动驾驶", "5G", "6G",
    # AI
    "AI", "人工智能", "大模型", "ChatGPT", "GPT", "deepseek", "DeepSeek", "Gemini",
    "机器学习", "深度学习", "神经网络", "算法", "数据", "云计算", "算力", "Sora", "Claude",
    # 教育
    "教育", "高考", "考研", "大学", "学校", "学生", "老师", "教师", "课程", "培训",
    "留学", "招生", "毕业", "论文", "学术", "科研",
]

def is_relevant(title: str) -> bool:
    title_lower = title.lower()
    return any(kw.lower() in title_lower for kw in KEYWORDS)

def make_create_link(trending_url: str) -> str:
    """把 trending URL 转成去创作链接"""
    # https://www.toutiao.com/trending/7624945441272463414
    # → https://mp.toutiao.com/profile_v4/graphic/publish?hot_selection_id=7624945441272463414&enter_from=inspiration
    m = re.search(r'/trending/(\d+)', trending_url)
    if m:
        return f"https://mp.toutiao.com/profile_v4/graphic/publish?hot_selection_id={m.group(1)}&enter_from=inspiration"
    return trending_url

def scrape_items(page) -> list[dict]:
    """点击加载更多直到全部加载，抓取所有热选条目（最多100条）"""
    items = []

    # 等待热选列表加载
    page.wait_for_selector("a[href*='toutiao.com/trending/']", timeout=15000)

    # 循环点击"加载更多"直到没有为止
    while True:
        more_btn = page.query_selector("text=加载更多")
        if not more_btn:
            break
        more_btn.click()
        page.wait_for_timeout(1500)

    # 抓取所有条目
    links = page.query_selector_all("a[href*='toutiao.com/trending/']")
    for link in links:
        trending_url = link.get_attribute("href") or ""
        text = link.inner_text().strip()

        lines = [l.strip() for l in text.splitlines() if l.strip()]
        if len(lines) < 4:
            continue

        title = lines[0]
        rank = next((l for l in lines if any(k in l for k in ["榜第", "热榜第"])), "")
        reads_m = re.search(r'阅读\s*([\d.,万]+)', text)
        reads = reads_m.group(1) if reads_m else "-"
        discuss_m = re.search(r'讨论\s*([\d,]+)', text)
        discuss = discuss_m.group(1) if discuss_m else "-"

        if title and trending_url:
            items.append({
                "title": title,
                "rank": rank,
                "reads": reads,
                "discuss": discuss,
                "link": make_create_link(trending_url),
                "relevant": is_relevant(title),
            })

    return items

def save_markdown(items: list[dict], timestamp: datetime) -> tuple[str, list]:
    date_str = timestamp.strftime("%Y-%m-%d")
    time_str = timestamp.strftime("%H:%M")
    filename = f"data/{date_str}.md"
    os.makedirs("data", exist_ok=True)

    relevant = [i for i in items if i["relevant"]]

    section = f"\n\n## {date_str} {time_str}\n\n"

    section += "### 科技 / AI / 教育相关\n\n"
    if relevant:
        section += "| 标题 | 榜单 | 阅读 | 讨论 | 链接 |\n"
        section += "|------|------|------|------|------|\n"
        for i in relevant:
            section += f"| {i['title']} | {i['rank']} | {i['reads']} | {i['discuss']} | [去创作]({i['link']}) |\n"
    else:
        section += "本次无相关话题。\n"

    section += "\n### 全部热选话题\n\n"
    section += "| 标题 | 榜单 | 阅读 | 讨论 | 链接 |\n"
    section += "|------|------|------|------|------|\n"
    for i in items:
        section += f"| {i['title']} | {i['rank']} | {i['reads']} | {i['discuss']} | [去创作]({i['link']}) |\n"

    if os.path.exists(filename):
        with open(filename, "a", encoding="utf-8") as f:
            f.write(section)
    else:
        with open(filename, "w", encoding="utf-8") as f:
            f.write(f"# 头条热选 {date_str}\n")
            f.write(section)

    return filename, relevant

def update_readme(date_str: str, time_str: str, total: int, relevant_count: int):
    entry = f"| {date_str} {time_str} | {total} | {relevant_count} | [查看](data/{date_str}.md) |\n"
    readme = "README.md"
    if not os.path.exists(readme):
        with open(readme, "w", encoding="utf-8") as f:
            f.write("# 头条热选归档\n\n每2小时自动抓取，过滤科技/AI/教育相关话题。\n\n")
            f.write("| 时间 | 总话题数 | 相关话题数 | 详情 |\n")
            f.write("|------|---------|-----------|------|\n")
            f.write(entry)
    else:
        with open(readme, "a", encoding="utf-8") as f:
            f.write(entry)

def git_push(timestamp: datetime, total: int, relevant_count: int):
    msg = f"抓取 {timestamp.strftime('%Y-%m-%d %H:%M')} | {total}条 | 相关{relevant_count}条"
    os.system("git add -A")
    os.system(f'git commit -m "{msg}"')
    ret = os.system("git push origin HEAD 2>&1")
    if ret != 0:
        print("⚠️  推送失败，请检查 git 认证配置")

def main():
    if not os.path.exists(AUTH_DIR):
        print("错误：未找到登录状态，请先运行 login.py")
        sys.exit(1)

    print(f"[{datetime.now()}] 开始抓取...")
    os.chdir(os.path.dirname(os.path.abspath(__file__)))

    with sync_playwright() as p:
        browser = p.chromium.launch_persistent_context(
            user_data_dir=AUTH_DIR,
            headless=True,
        )
        page = browser.new_page()
        page.goto(URL, wait_until="networkidle", timeout=30000)

        # 检查是否已登录
        if "login" in page.url or "auth" in page.url:
            print("错误：登录状态已过期，请重新运行 login.py")
            browser.close()
            sys.exit(1)

        items = scrape_items(page)
        browser.close()

    if not items:
        print("错误：未抓取到任何条目")
        sys.exit(1)

    timestamp = datetime.now()
    filename, relevant = save_markdown(items, timestamp)
    update_readme(
        timestamp.strftime("%Y-%m-%d"),
        timestamp.strftime("%H:%M"),
        len(items), len(relevant)
    )

    print(f"✓ 共 {len(items)} 条，科技/AI/教育相关 {len(relevant)} 条")
    for r in relevant:
        print(f"  → {r['title']} ({r['rank']}) 阅读{r['reads']}")

    git_push(timestamp, len(items), len(relevant))
    print(f"✓ 已保存并推送：{filename}")

if __name__ == "__main__":
    main()
