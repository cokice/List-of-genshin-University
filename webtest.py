import requests
import re
import shutil

# Markdown 文件路径
file_path = 'README.md'
backup_path = 'README_backup.md'

# 备份原始 Markdown 文件
shutil.copy(file_path, backup_path)

# 读取 Markdown 文件内容
with open(file_path, 'r', encoding='utf-8') as file:
    content = file.read()

# 正则表达式匹配 Markdown 链接
links = re.findall(r'\[([^\]]+)\]\((https?://[^\s]+)\)', content)

# 初始化一个空字符串存储修改后的内容
modified_content = content

# 测试每个链接并添加状态
for link_text, url in links:
    try:
        response = requests.get(url, timeout=10)  # 设置超时时间为10秒
        if response.status_code == 200:
            status = '✅'
        else:
            status = '❌'
    except requests.exceptions.RequestException:
        status = '❌'
    
    # 构造带有状态的链接标记
    modified_link = f'[{link_text}]({url}) {status}'
    # 替换原始内容中的链接为带状态的链接
    modified_content = modified_content.replace(f'[{link_text}]({url})', modified_link)

# 写入修改后的 Markdown 文件
with open(file_path, 'w', encoding='utf-8') as file:
    file.write(modified_content)

print('链接状态检查完成，结果已保存。')
