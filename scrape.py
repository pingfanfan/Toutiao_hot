#!/usr/bin/env python3
"""
抓取头条号热选榜，过滤科技/AI/教育相关话题，保存到 data/ 目录并推送 GitHub。
每2小时运行一次（由 launchd 调度）。
"""
import os
import re
import sys
import json
import subprocess
from datetime import datetime
from playwright.sync_api import sync_playwright

AUTH_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".auth")
URL = "https://mp.toutiao.com/profile_v4/activity/hot-selection"
OPENMONEY_DIR = os.path.expanduser("~/Desktop/AK/OpenMoney")
CLAUDE_BIN = "/Users/pingfan/Library/Application Support/Claude/claude-code/2.1.87/claude.app/Contents/MacOS/claude"
LAST_TOPICS_FILE = "data/last_topics.json"

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

def update_readme(date_str: str, time_str: str, total: int, relevant_count: int, relevant: list):
    readme = "README.md"
    header = (
        "# 头条热选归档\n\n"
        "每2小时自动抓取，过滤科技/AI/教育相关话题。\n\n"
    )
    table_header = (
        "| 时间 | 科技/AI/教育话题 | 总话题数 | 详情 |\n"
        "|------|-----------------|---------|------|\n"
    )

    # 科技/AI/教育话题列：显示标题列表
    if relevant:
        topics_cell = "<br>".join(f"[{i['title']}]({i['link']})" for i in relevant)
    else:
        topics_cell = "（无）"

    new_entry = f"| {date_str} {time_str} | {topics_cell} | {total} | [查看](data/{date_str}.md) |\n"

    if not os.path.exists(readme):
        with open(readme, "w", encoding="utf-8") as f:
            f.write(header + table_header + new_entry)
    else:
        with open(readme, "r", encoding="utf-8") as f:
            content = f.read()
        # 在表头之后插入新条目（最新在最前）
        insert_after = table_header
        if insert_after in content:
            content = content.replace(insert_after, insert_after + new_entry, 1)
        else:
            # 表头不存在时重建
            content = header + table_header + new_entry
        with open(readme, "w", encoding="utf-8") as f:
            f.write(content)

def load_last_topics() -> set:
    """加载上次抓取的话题标题集合"""
    if not os.path.exists(LAST_TOPICS_FILE):
        return set()
    with open(LAST_TOPICS_FILE, encoding="utf-8") as f:
        return set(json.load(f))

def save_last_topics(items: list[dict]):
    """保存本次所有话题标题，供下次对比"""
    os.makedirs("data", exist_ok=True)
    with open(LAST_TOPICS_FILE, "w", encoding="utf-8") as f:
        json.dump([i["title"] for i in items], f, ensure_ascii=False)

def find_new_topics(items: list[dict], last_titles: set) -> list[dict]:
    """返回本次新出现的相关话题"""
    return [i for i in items if i["relevant"] and i["title"] not in last_titles]

def trigger_write_all(topic: dict):
    """在 OpenMoney 目录里调用 claude -p 执行 /write-all"""
    prompt = (
        f"/write-all 话题：{topic['title']}\n"
        f"榜单：{topic['rank']}，阅读量：{topic['reads']}，讨论数：{topic['discuss']}\n"
        f"去创作链接：{topic['link']}"
    )
    print(f"  → 触发写作：{topic['title']}")
    try:
        subprocess.Popen(
            [CLAUDE_BIN, "-p", prompt, "--dangerously-skip-permissions"],
            cwd=OPENMONEY_DIR,
            stdout=open(os.path.join(OPENMONEY_DIR, f"logs/write-{datetime.now().strftime('%Y%m%d-%H%M')}-{topic['title'][:20]}.log"), "w"),
            stderr=subprocess.STDOUT,
        )
    except Exception as e:
        print(f"  ⚠️  触发失败：{e}")

def notify(title: str, message: str):
    os.system(f'osascript -e \'display notification "{message}" with title "{title}" sound name "Glass"\'')

def git_push(timestamp: datetime, total: int, relevant_count: int, new_topics: list):
    msg = f"抓取 {timestamp.strftime('%Y-%m-%d %H:%M')} | {total}条 | 相关{relevant_count}条"
    os.system("git add -A")
    os.system(f'git commit -m "{msg}"')
    ret = os.system("git push origin HEAD 2>&1")
    if ret != 0:
        print("⚠️  推送失败，请检查 git 认证配置")
        notify("头条热选 ⚠️", "GitHub 推送失败，请检查网络")
    else:
        if new_topics:
            topics_str = "、".join(t["title"][:10] for t in new_topics[:3])
            suffix = f" 等{len(new_topics)}条新话题" if len(new_topics) > 1 else ""
            notify("头条热选已更新 ★", f"新增：{topics_str}{suffix}")
        else:
            notify("头条热选已更新", f"共{total}条，相关{relevant_count}条，无新增话题")

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

    # 检测新话题
    last_titles = load_last_topics()
    new_topics = find_new_topics(items, last_titles)
    save_last_topics(items)

    filename, relevant = save_markdown(items, timestamp)
    update_readme(
        timestamp.strftime("%Y-%m-%d"),
        timestamp.strftime("%H:%M"),
        len(items), len(relevant), relevant
    )

    print(f"✓ 共 {len(items)} 条，科技/AI/教育相关 {len(relevant)} 条")
    for r in relevant:
        print(f"  {'★ 新增' if r in new_topics else '  '} {r['title']} ({r['rank']}) 阅读{r['reads']}")

    # 对新增相关话题触发写作
    if new_topics:
        print(f"\n★ 发现 {len(new_topics)} 个新话题，触发 /write-all...")
        os.makedirs(os.path.join(OPENMONEY_DIR, "logs"), exist_ok=True)
        for topic in new_topics:
            trigger_write_all(topic)
    else:
        print("  （无新增相关话题）")

    git_push(timestamp, len(items), len(relevant), new_topics)
    print(f"✓ 已保存并推送：{filename}")

if __name__ == "__main__":
    main()
