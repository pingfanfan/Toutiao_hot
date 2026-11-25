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
import glob
import shutil
import time
import urllib.request
from datetime import datetime
from playwright.sync_api import sync_playwright

AUTH_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".auth")
URL = "https://mp.toutiao.com/profile_v4/activity/hot-selection"
OPENMONEY_DIR = os.path.expanduser("~/Desktop/AK/OpenMoney")
# 桌面端的目录结构会变：老版本是 <版本>/claude.app，新版本是 <版本>/<哈希>/claude.app
CLAUDE_BIN_GLOBS = [
    os.path.expanduser("~/Library/Application Support/Claude/claude-code/*/claude.app/Contents/MacOS/claude"),
    os.path.expanduser("~/Library/Application Support/Claude/claude-code/*/*/claude.app/Contents/MacOS/claude"),
]
MAX_ATTEMPTS = 3


class LoginExpired(Exception):
    pass


def find_claude_bin():
    """Claude 桌面端升级后旧版本目录会被删除，所以每次动态找最新版本"""
    def ver(path):
        v = path.split("/claude-code/")[1].split("/")[0]
        return tuple(int(x) if x.isdigit() else 0 for x in v.split("."))
    candidates = sorted((c for g in CLAUDE_BIN_GLOBS for c in glob.glob(g)), key=ver)
    if candidates:
        return candidates[-1]
    # launchd 的 PATH 里没有 ~/.local/bin，单独找一下命令行版
    local_bin = os.path.expanduser("~/.local/bin/claude")
    if os.access(local_bin, os.X_OK):
        return local_bin
    return shutil.which("claude")
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

    # 循环点击"加载更多"直到没有为止。
    # 用 locator 而不是 ElementHandle：列表重新渲染后旧句柄会失效
    # （"Element is not attached to the DOM"），locator 每次都会重新查找。
    # 点击后按钮会先消失、新条目稍后才渲染出来，所以不能看到按钮消失就停，
    # 要等条目数连续几秒不再增长才算加载完。
    link_sel = "a[href*='toutiao.com/trending/']"
    for _ in range(30):
        before = page.locator(link_sel).count()
        more_btn = page.locator("text=加载更多").first
        if more_btn.count() > 0:
            try:
                more_btn.click(timeout=5000)
            except Exception as e:
                print(f"  （加载更多点击失败，重试：{e.__class__.__name__}）")
        else:
            page.mouse.wheel(0, 5000)
        grew = False
        for _ in range(16):
            page.wait_for_timeout(500)
            if page.locator(link_sel).count() > before:
                grew = True
                break
        if not grew and page.locator("text=加载更多").count() == 0:
            break
    page.wait_for_timeout(1000)

    # 抓取所有条目
    links = page.query_selector_all("a[href*='toutiao.com/trending/']")
    seen = set()
    for link in links:
        trending_url = link.get_attribute("href") or ""
        # 加载更多时页面偶尔会把同一条再渲染一次
        if trending_url in seen:
            continue
        seen.add(trending_url)
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
    claude_bin = find_claude_bin()
    if not claude_bin:
        print("  ⚠️  未找到 claude 可执行文件，跳过写作")
        return
    try:
        subprocess.Popen(
            [claude_bin, "-p", prompt, "--dangerously-skip-permissions"],
            cwd=OPENMONEY_DIR,
            stdout=open(os.path.join(OPENMONEY_DIR, f"logs/write-{datetime.now().strftime('%Y%m%d-%H%M')}-{topic['title'][:20]}.log"), "w"),
            stderr=subprocess.STDOUT,
        )
    except Exception as e:
        print(f"  ⚠️  触发失败：{e}")

def load_env():
    env_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    env = {}
    if os.path.exists(env_file):
        for line in open(env_file):
            line = line.strip()
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip()
    return env

def post_github_comment(token: str, issue_number: int, body: str):
    url = f"https://api.github.com/repos/pingfanfan/Toutiao_hot/issues/{issue_number}/comments"
    data = json.dumps({"body": body}).encode()
    req = urllib.request.Request(url, data=data, headers={
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github.v3+json",
        "Content-Type": "application/json",
    })
    try:
        urllib.request.urlopen(req, timeout=10)
        print("✓ GitHub Issue 通知已发送")
    except Exception as e:
        print(f"⚠️  GitHub 通知失败：{e}")

def git_push(timestamp: datetime, total: int, relevant_count: int, new_topics: list, relevant: list):
    msg = f"抓取 {timestamp.strftime('%Y-%m-%d %H:%M')} | {total}条 | 相关{relevant_count}条"
    os.system("git add -A")
    os.system(f'git commit -m "{msg}"')
    ret = os.system("git push origin HEAD 2>&1")
    if ret != 0:
        print("⚠️  推送失败，请检查 git 认证配置")
        return

    # 发 GitHub Issue 评论
    env = load_env()
    token = env.get("GITHUB_TOKEN")
    issue_number = int(env.get("GITHUB_ISSUE_NUMBER", 1))
    if not token:
        return

    ts = timestamp.strftime("%Y-%m-%d %H:%M")
    date_str = timestamp.strftime("%Y-%m-%d")

    if new_topics:
        new_lines = "\n".join(f"- **{t['title']}**（{t['rank']}，阅读 {t['reads']}）[去创作]({t['link']})" for t in new_topics)
        comment = f"## ★ {ts} 新增话题\n\n{new_lines}\n\n[查看完整列表](data/{date_str}.md)"
    else:
        rel_lines = "\n".join(f"- {t['title']}（{t['rank']}，阅读 {t['reads']}）" for t in relevant) if relevant else "- 无"
        comment = f"## {ts} 更新\n\n共 {total} 条，科技/AI/教育相关 {relevant_count} 条，无新增话题。\n\n**相关话题：**\n{rel_lines}\n\n[查看完整列表](data/{date_str}.md)"

    post_github_comment(token, issue_number, comment)

def notify_failure(reason: str):
    """抓取失败时发 Issue 评论，免得挂了好几个月都没人发现"""
    env = load_env()
    token = env.get("GITHUB_TOKEN")
    if not token:
        return
    issue_number = int(env.get("GITHUB_ISSUE_NUMBER", 1))
    ts = datetime.now().strftime("%Y-%m-%d %H:%M")
    post_github_comment(token, issue_number, f"## ⚠️ {ts} 抓取失败\n\n{reason}")


def fetch_items() -> list[dict]:
    with sync_playwright() as p:
        browser = p.chromium.launch_persistent_context(
            user_data_dir=AUTH_DIR,
            headless=True,
        )
        try:
            page = browser.new_page()
            # 不要用 networkidle：头条号后台有持续的埋点/轮询请求，
            # 网络永远不会"空闲"，这是 30s 超时的主要原因。
            page.goto(URL, wait_until="domcontentloaded", timeout=60000)

            # 等热选列表出现；超时则判断是不是被踢到了登录页
            try:
                page.wait_for_selector("a[href*='toutiao.com/trending/']", timeout=30000)
            except Exception:
                if "login" in page.url or "auth" in page.url \
                        or page.locator("input[type='password']").count() > 0:
                    raise LoginExpired(page.url)
                raise

            return scrape_items(page)
        finally:
            browser.close()


def main():
    if not os.path.exists(AUTH_DIR):
        print("错误：未找到登录状态，请先运行 login.py")
        sys.exit(1)

    print(f"[{datetime.now()}] 开始抓取...")
    os.chdir(os.path.dirname(os.path.abspath(__file__)))

    items = []
    last_err = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            items = fetch_items()
            if items:
                break
            last_err = "未抓取到任何条目"
        except LoginExpired as e:
            print(f"错误：登录状态已过期（{e}），请重新运行 login.py")
            notify_failure("登录状态已过期，请在 Mac 上运行 `python3 login.py` 重新登录。")
            sys.exit(1)
        except Exception as e:
            last_err = f"{e.__class__.__name__}: {str(e).splitlines()[0]}"
        print(f"  第 {attempt} 次尝试失败：{last_err}")
        if attempt < MAX_ATTEMPTS:
            time.sleep(30)

    if not items:
        print(f"错误：{MAX_ATTEMPTS} 次尝试均失败：{last_err}")
        notify_failure(f"{MAX_ATTEMPTS} 次尝试均失败：`{last_err}`")
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

    git_push(timestamp, len(items), len(relevant), new_topics, relevant)
    print(f"✓ 已保存并推送：{filename}")

if __name__ == "__main__":
    main()
