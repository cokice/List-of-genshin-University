import concurrent.futures
import datetime
import dns.resolver
import functools
import idna
import io
import os
import pypinyin
import pypinyin.contrib.tone_convert
import pypinyin.style
import re
import requests
import subprocess
import sys
import threading
import traceback
import warnings


print_lock = threading.Lock()


def stack_func_stdout(func):
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        temp_stdout = io.StringIO()
        kwargs["file"] = temp_stdout
        try:
            ret = func(*args, **kwargs)
        except Exception as e:
            with print_lock:
                print("-" * 20, "Error occurred:", file=sys.stdout, flush=True)
                print(traceback.format_exc(), file=sys.stderr, flush=True)
                print("-" * 20, "Output:", file=sys.stdout, flush=True)
                print(temp_stdout.getvalue(), end="", file=sys.stdout, flush=True)
            sys.exit(1)
        with print_lock:
            print(temp_stdout.getvalue(), end="", file=sys.stdout, flush=True)
        return ret

    return wrapper


def resub_concurrent(pattern, repl, string, count=0, flags=0, thread_count=16):
    assert callable(repl)
    pool = concurrent.futures.ThreadPoolExecutor(thread_count)
    futures = []
    replaced = 0
    while string and (count == 0 or replaced < count):
        m = re.search(pattern, string, flags)
        if m:
            if m.start() == 0:
                futures.append(pool.submit(repl, m))
                string = string[m.end() :]
                replaced += 1
            else:
                futures.append(string[: m.start()])
                futures.append(pool.submit(repl, m))
                string = string[m.end() :]
                replaced += 1
        else:
            futures.append(string)
            break
    out = ""
    for future in futures:
        if isinstance(future, str):
            out += future
        else:
            out += future.result()
    return out


warnings.filterwarnings("ignore", category=requests.packages.urllib3.exceptions.InsecureRequestWarning)
sys.setrecursionlimit(64)

resolver = dns.resolver.Resolver()
resolver.nameservers += ["114.114.114.114", "8.8.8.8"]

header = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36 Edg/123.0.0.0"
}

# Get proxies
with open("proxies.txt", "rt", encoding="utf-8") as f:
    proxies = list(i for i in map(str.strip, f.read().splitlines()) if i and not i.startswith("#"))
proxies = [None] + list(map(lambda x: {"http": x, "https": x}, proxies))

# Get the list
with open("README.md", "rt", encoding="utf-8") as f:
    md_content = f.read()

# Disable retry
s = requests.Session()
a = requests.adapters.HTTPAdapter(max_retries=2)
s.mount("http://", a)
s.mount("https://", a)


# Print in red
def print_error(*s, **kwargs):
    if "end" in kwargs:
        end = kwargs["end"]
        del kwargs["end"]
    else:
        end = "\n"
    if "flush" in kwargs:
        del kwargs["flush"]
    print("\033[31m", end="", flush=True, **kwargs)
    if s:
        print(*s, end="", flush=True, **kwargs)
    else:
        print("Error", end="", flush=True, **kwargs)
    print("\033[0m", end=end, flush=True, **kwargs)


# Print in green
def print_success(*s, **kwargs):
    if "end" in kwargs:
        end = kwargs["end"]
        del kwargs["end"]
    else:
        end = "\n"
    if "flush" in kwargs:
        del kwargs["flush"]
    print("\033[32m", end="", flush=True, **kwargs)
    if s:
        print(*s, end="", flush=True, **kwargs)
    else:
        print("Success", end="", flush=True, **kwargs)
    print("\033[0m", end=end, flush=True, **kwargs)


@stack_func_stdout
def replace_table_row(m: re.Match, file=sys.stdout):
    print(file=file)
    url = m.group(2)
    if not re.match(r"https?://", url):
        url = "http://" + url
    url_split = url.split("/")
    url_split[2] = idna.decode(url_split[2])
    url = "/".join(url_split)
    successes = {0: [], 1: [], 2: []}
    success, method = check_url(url, file=file)
    if success == 1:
        if method[0] == "Unknown":
            method = ("", None)
        elif method[0] == "EDU Domain":
            return f"| [{m.group(1)}]({url}) | {m.group(3)} | :warning: {method[0]} |"
        suggestion_removed = re.sub(r"\*.*?\*", "", m.group(3))
        return f"| [{m.group(1)}]({url}) | {suggestion_removed} | :white_check_mark: {method[0]} |"
    else:
        successes[success].append((url, method))
        print_error("Failed, trying other possible URLs", file=file)
        for new_url in get_other_possible_url(url):
            success, method1 = check_url(new_url, file=file)
            if success == 1:
                if method1[0] == "Unknown":
                    method1 = ("", None)
                suggestion_removed = re.sub(r"\*.*?\*", "", m.group(3))
                return f"| [{m.group(1)}]({new_url}) | {suggestion_removed} | :white_check_mark: {method1[0]} |"
            successes[success].append((new_url, method1))
        if successes[2]:
            if sum(1 for i in successes[2] if i[1] == "NXDOMAIN") != len(successes[2]):
                i = 0
                while successes[2][i][1] == "NXDOMAIN":
                    i += 1
                return f"| [{m.group(1)}]({successes[2][i][0]}) | {m.group(3)} | :question: {successes[2][i][1]} |"
        return f"| [{m.group(1)}]({url}) | {m.group(3)} | :x: {method} |"


# Try to remove or add https and www.
def get_other_possible_url(url):
    assert re.match(r"https?://", url)
    new_urls = []
    if re.match(r"https?://www\.", url):
        new_urls.append(re.sub(r"https?://www\.", "http://", url))
        new_urls.append(re.sub(r"https?://www\.", "https://", url))
        if url.startswith("https://"):
            new_urls.append("http://" + url[8:])
        else:
            new_urls.append("https://" + url[7:])
    else:
        new_urls.append(re.sub(r"https?://", "http://www.", url))
        new_urls.append(re.sub(r"https?://", "https://www.", url))
        if url.startswith("https://"):
            new_urls.append("http://" + url[8:])
        else:
            new_urls.append("https://" + url[7:])
    return sorted(new_urls, reverse=True)


def get_domain(url):
    if not re.match(r"https?://", url):
        url = "http://" + url
    return url.split("/")[2]


def check_url(url, ignore_ssl=False, file=sys.stdout):
    global proxies, resolver
    print(f"Checking [{url}]...", end=" ", flush=True, file=file)
    method = ("", None)
    if "edu" in get_domain(url) and os.environ.get("NO_SKIP_EDU") not in ("1", "true", "True"):
        print_success("EDU domain, skipped", file=file)
        return 1, ("EDU Domain", None)
    error = "Unknown error"
    try:
        res = resolver.resolve(get_domain(url), "CNAME")
        print(f"CNAME to [{res[0].target.to_text()[:-1]}]", end=" ", flush=True, file=file)
        method = ("CNAME", res[0].target.to_text()[:-1])
    except dns.resolver.NoAnswer:
        pass
    except dns.resolver.NXDOMAIN:
        print("CNAME NXDOMAIN", end=" ", flush=True, file=file)
    except:
        print("DNS CNAME error", end=" ", flush=True, file=file)
        error = "DNS CNAME error"
    if not method[0]:
        try:
            res = resolver.resolve(get_domain(url), "A")
            print(f"A to [{res[0].address}]", end=" ", flush=True, file=file)
            method = ("Unknown", res[0].address)
        except dns.resolver.NoAnswer:
            print("A NXDOMAIN", end=" ", flush=True, file=file)
            error = "NXDOMAIN"
        except dns.resolver.NXDOMAIN:
            print("DNS A error", end=" ", flush=True, file=file)
            error = "NXDOMAIN"
        except:
            print("DNS A error", end=" ", flush=True, file=file)
            error = "DNS error"
    if method[0]:
        for idx, p in enumerate(proxies):
            try:
                if p is not None:
                    print(f"  -- Using proxy {idx}...", end=" ", flush=True, file=file)
                r = s.get(url, allow_redirects=False, timeout=3, verify=not ignore_ssl, proxies=p)
                if not 200 <= r.status_code < 400:
                    print_error(f"Failed with status code {r.status_code}", file=file)
                    error = str(r.status_code)
                    return 0, error
                elif 300 <= r.status_code < 400:
                    target = r.headers["Location"]
                    if not re.match(r"https?://", target):
                        if target.startswith("/"):
                            target = "/".join(url.split("/")[:3]) + target
                        elif url.split("#")[0].split("?")[0].endswith("/"):
                            target = url.split("#")[0].split("?")[0] + target
                        else:
                            target = "/".join(url.split("#")[0].split("?")[0].split("/")[:-1]) + "/" + target
                    print(f"Redirect to [{target}] with status code {r.status_code}", file=file)
                    print("-- Checking redirect...", end=" ", flush=True, file=file)
                    success, submethod = check_url(target, file=file)
                    if method[0] == "CNAME" and (get_domain(url) in target or get_domain(target) in url):
                        method = (submethod[0], method[1])
                    if success != 1:
                        return 2, submethod
                    method = (f"Redirect {r.status_code}", target)
                elif 'http-equiv="refresh"' in r.text:
                    target = re.search(r'content="\d+; *url=(.*?)"', r.text, re.I).group(1)
                    if not re.match(r"https?://", target):
                        if target.startswith("/"):
                            target = "/".join(url.split("/")[:3]) + target
                        elif url.split("#")[0].split("?")[0].endswith("/"):
                            target = url.split("#")[0].split("?")[0] + target
                        else:
                            target = "/".join(url.split("#")[0].split("?")[0].split("/")[:-1]) + "/" + target
                    print(f"Redirect with meta refresh to [{target}]", file=file)
                    print("-- Checking redirect...", end=" ", flush=True, file=file)
                    success, submethod = check_url(target, file=file)
                    if method[0] == "CNAME":
                        method = (submethod[0], method[1])
                    if success != 1:
                        return 2, submethod
                    method = ("Meta Refresh", target)
                elif method[0] == "CNAME":
                    print_success("CNAME Success", file=file)
                else:
                    print_success("Unknown redirect method or no redirect", file=file)
                return 1, method
            except requests.exceptions.SSLError as e:
                if ignore_ssl:
                    print_error(f"Failed with exception {e.__class__.__name__}", file=file)
                    return 0, e.__class__.__name__
                if traceback.extract_stack()[-2].name == "check_url":
                    print("\r-- Checking redirect... ", end="", flush=True, file=file)
                else:
                    print("\r", end="", flush=True)
                print_error("SSLError, retrying without SSL...", end=" ", flush=True, file=file)
                return check_url(url, True, file=file)
            except requests.exceptions.RequestException as e:
                print_error(f"Failed with exception {e.__class__.__name__}", file=file)
                error = "Connection error"
    else:
        print_error(file=file)
        return 0, error
    return 0, error


@pypinyin.style.register("tone_with_original")
def tone_with_original(pinyin, han, **kwargs):
    return [pypinyin.contrib.tone_convert.to_tone3(pinyin), han]


def handle_no_pinyin(s):
    if sum(1 for i in s if 65 <= ord(i) <= 90 or 97 <= ord(i) <= 122) == 0:
        return [[[i]] for i in s]
    return [[["", i]] for i in s]


def reshape_pinyin(l):
    o = []
    first = []
    second = []
    for i in l:
        if len(i[0]) == 1 or i[0][0] == "":
            if first:
                o.append(first)
                o.append(second)
                first = []
                second = []
            o.append(i[0])
        else:
            first.append(i[0][0])
            second.append(i[0][1])
    if first:
        o.append(first)
        o.append(second)
    return o


def sort_key(s):
    m = re.match(r"\| *\[(.*?)\]\((?P<link>.*?)\) *\| *(?P<name>.*?) *\|.*", s)
    return [
        reshape_pinyin(pypinyin.pinyin(m["name"], style="tone_with_original", errors=handle_no_pinyin)),
        reshape_pinyin(pypinyin.pinyin(m["link"], style="tone_with_original", errors=handle_no_pinyin)),
    ]


# Detect new merged PR
new = []
remove_pending = []
co_authors = set()
for i in os.listdir("new"):
    if not i.endswith(".add.md"):
        continue
    with open(os.path.join("new", i), "rt", encoding="utf-8") as f:
        add_content = f.read()
    while m := re.search(r"\| *\[(.*?)\]\((.*?)\) *\| *(.*?) *\|.*", add_content):
        new.append(replace_table_row(m))
        add_content = add_content[m.end() :]
    l = subprocess.check_output(["git", "log", os.path.join("new", i)]).decode()
    co_authors.update(
        [
            i
            for i in (
                re.findall(r"(?<=Author: ).*? <.*?>", l)
                + re.findall(r"(?<=Co-authored-by: ).*? <.*?>", l)
                + re.findall(r"(?<=Signed-off-by: ).*? <.*?>", l)
            )
            if not "[bot]" in i
        ]
    )
    remove_pending.append(os.path.join("new", i))


if not "COUNT_ONLY" in os.environ or os.environ["COUNT_ONLY"] not in ("1", "true", "True"):
    md_out = resub_concurrent(r"\| *\[(.*?)\]\((.*?)\) *\| *(.*?) *\|.*", replace_table_row, md_content)
    print("Sorting...")
    m = re.match(
        r"(?P<header>.*?)(?P<table>(\| *\[[^\n]*?\]\([^\n]*?\) *\|[^\n]*?\|[^\n]*?\|\n?)+)(?P<footer>.*)",
        md_out,
        re.DOTALL,
    )
    table = m["table"].splitlines(False) + new
    md_out = m["header"] + "\n".join(sorted(set(table), key=sort_key)) + "\n" + m["footer"]
else:
    md_out = md_content
md_out = re.sub(r"目前有\d+", "目前有%d" % len(re.findall(r"\| *\[(.*?)\]\((.*?)\) *\| *(.*?) *\|.*", md_out)), md_out)
md_out = re.sub(
    r"其中\d+个有效",
    "其中%d个有效" % len(re.findall(r"\| *\[(.*?)\]\((.*?)\) *\| *(.*?) *\|.*(:white_check_mark:|:question:)", md_out)),
    md_out,
)
now = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8))).strftime("%Y-%m-%d %H:%M UTC+8")
md_out = re.sub(r"\d{4}-\d{2}-\d{2}.*?(?=）)", now, md_out, count=1)
with open("README.md", "wt", encoding="utf-8") as f:
    f.write(md_out)

# Detect GitHub Actions
if "GITHUB_ACTIONS" in os.environ and os.environ["GITHUB_ACTIONS"] == "true":
    print("::group::Output Markdown")
    print(md_out)
    print("::endgroup::")
else:
    print("-" * 20, "Output Markdown")
    print(md_out)

# Generate commit message
commit_message = f"Update Status ({now})\n\n\n"
for i in co_authors:
    commit_message += f"Co-authored-by: {i}\n"
print("Commit message:", commit_message)
delimiter = "EOF"
while delimiter in commit_message:
    delimiter += "EOF"
os.system(f'echo "commit_message<<{delimiter}" >> "$GITHUB_OUTPUT"')
for i in commit_message.splitlines(False):
    os.system(f'echo "{i}" >> "$GITHUB_OUTPUT"')
os.system(f'echo "{delimiter}" >> "$GITHUB_OUTPUT"')

for i in remove_pending:
    os.remove(i)
