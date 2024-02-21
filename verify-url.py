import concurrent.futures
import datetime
import dns.resolver
import functools
import io
import os
import re
import requests
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
        ret = func(*args, **kwargs)
        with print_lock:
            print(temp_stdout.getvalue(), end="", file=sys.stdout, flush=True)
        return ret

    return wrapper


def resub_concurrent(pattern, repl, string, count=0, flags=0, thread_count=8):
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
    successes = {0: [], 1: [], 2: []}
    success, method = check_url(url, file=file)
    if success == 1:
        if method[0] == "Unknown":
            method = ("", None)
        return "|".join(m.group(0).split("|")[:3]) + f"| :white_check_mark: {method[0]} |"
    else:
        successes[success].append((url, method))
        print_error("Failed, trying other possible URLs", file=file)
        for new_url in get_other_possible_url(url):
            success, method1 = check_url(new_url, file=file)
            if success == 1:
                if method1[0] == "Unknown":
                    method1 = ("", None)
                return f"| [{m.group(1)}]({new_url}) | {m.group(3)} | :white_check_mark: {method1[0]} |"
            successes[success].append((new_url, method1))
        if successes[2]:
            if sum(1 for i in successes[2] if i[1] == "NXDOMAIN") != len(successes[2]):
                i = 0
                while successes[2][i][1] == "NXDOMAIN":
                    i += 1
                return f"| [{m.group(1)}]({successes[2][i][0]}) | {m.group(3)} | :question: {successes[2][i][1]} |"
        return "|".join(m.group(0).split("|")[:3]) + f"| :x: {method} |"


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


def check_url(url, ignore_ssl=False, file=sys.stdout):
    global proxies, resolver
    print(f"Checking [{url}]...", end=" ", flush=True, file=file)
    method = ("", None)
    if "edu" in url.split("/")[2] and os.environ.get("NO_SKIP_EDU") not in ("1", "true", "True"):
        print_success("EDU domain, skipped", file=file)
        return 1, method
    error = "Unknown error"
    try:
        res = resolver.resolve(url.split("/")[2], "CNAME")
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
            res = resolver.resolve(url.split("/")[2], "A")
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
                    if method[0] == "CNAME":
                        method = (submethod[0], method[1])
                    if success != 1:
                        return 2, submethod
                    method = (f"Redirect {r.status_code}", target)
                elif 'http-equiv="refresh"' in r.text:
                    target = re.search(r'content="\d+;url=(.*?)"', r.text, re.I).group(1)
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
                print_error(f"Failed with exception {e}", file=file)
                error = "Connection error"
    else:
        print_error(file=file)
        return 0, error
    return 0, error


if not "COUNT_ONLY" in os.environ or os.environ["COUNT_ONLY"] not in ("1", "true", "True"):
    md_out = resub_concurrent(r"\| *\[(.*?)\]\((.*?)\) *\| *(.*?) *\|.*", replace_table_row, md_content)
else:
    md_out = md_content
md_out = re.sub(r"目前有\d+", "目前有%d" % len(re.findall(r"\| *\[(.*?)\]\((.*?)\) *\| *(.*?) *\|.*", md_out)), md_out)
md_out = re.sub(
    r"其中\d+个有效",
    "其中%d个有效" % len(re.findall(r"\| *\[(.*?)\]\((.*?)\) *\| *(.*?) *\|.*(:white_check_mark:|:question:)", md_out)),
    md_out,
)
md_out = re.sub(r"\d{4}-\d{2}-\d{2}", datetime.date.today().isoformat(), md_out)
with open("README.md", "wt", encoding="utf-8") as f:
    f.write(md_out)
