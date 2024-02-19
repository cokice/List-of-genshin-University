package main

import (
	"crypto/tls"
	"fmt"
	"log"
	"net"
	"net/http"
	"os"
	"regexp"
	"strings"
	"sync"
	"time"
)

var logger = log.New(os.Stdout, "[main]", log.LstdFlags)
var client = &http.Client{
	Timeout: time.Second * 30,
	CheckRedirect: func(req *http.Request, via []*http.Request) error {
		return http.ErrUseLastResponse
	},
	Transport: &http.Transport{
		DialContext: (&net.Dialer{
			KeepAlive: -1,
		}).DialContext,
		TLSClientConfig: &tls.Config{
			InsecureSkipVerify: true,
		},
	},
}

type TestResult struct {
	domain string
	title  string
	name   string
	msg    string
	code   int
}

func main() {
	uList := FetchList()

	var res []*TestResult
	if len(uList) > 0 {
		var wg sync.WaitGroup
		wg.Add(len(uList))
		logger.Println("待测试数量：", len(uList))
		for _, v := range uList {
			r := &TestResult{domain: v[2], title: v[1], name: strings.Trim(v[3], " ")}
			res = append(res, r)
			go func(domain string, name string) {
				defer wg.Done()
				msg, code := TestDomain(domain)
				logger.Println(domain, name, msg, code)
				r.msg = msg
				r.code = code
			}(r.domain, r.name)
		}
		wg.Wait()
	} else {
		logger.Println("列表数量为0,请检查正则表达式")
	}
	for _, v := range res {
		logger.Printf("%s %s %s %d\n", v.domain, v.title, v.msg, v.code)
	}

	mdTable := ListToMDTable(res)
	ReplaceTable(mdTable)

}

func FetchList() (uList [][]string) {
	logger.Println("正在获取列表...")
	bytes, err := os.ReadFile("README.md")
	if err != nil {
		logger.Panicln("读取README.md文件失败")
	}
	re := regexp.MustCompile(`\[(.*)\]\((https?://.*\.[a-z]+/?)\)\s*\|\s*([\sa-zA-Z\x{3040}-\x{309F}\x{30A0}-\x{30FF}\x{4E00}-\x{9FFF}]+)\s*\|`)
	uList = re.FindAllStringSubmatch(string(bytes), -1)
	return
}

func TestDomain(domain string) (string, int) {
	res, err := client.Get(domain)
	if err != nil {
		logger.Println(domain, err)
		return "Failed", 0
	}
	defer res.Body.Close()
	return "Success", res.StatusCode
}

func ListToMDTable(result []*TestResult) string {
	var data string
	data += "| 网站 | 大学 | 状态 |\n"
	data += "| --- | --- | --- |\n"
	for _, res := range result {
		status := "✅"
		if res.code == 0 {
			status = "❌"
		}
		data += fmt.Sprintf("| [%s](%s) | %s | %s |\n", res.title, res.domain, res.name, status)
	}
	data += "\n\n"
	return data
}

func ReplaceTable(table string) {
	bytes, err := os.ReadFile("README.md")
	if err != nil {
		logger.Panicln("读取README.md文件失败")
	}
	content := string(bytes)
	head := strings.Index(content, "## 高校名单")
	tail := strings.Index(content, "##")
	if head == -1 || tail == -1 {
		// 中止替换
		return
	}
	loc, _ := time.LoadLocation("Asia/Shanghai")
	title := fmt.Sprintf("## 高校名单(%s)\n", time.Now().In(loc).Format("2006-01-02"))
	content = strings.ReplaceAll(content, content[head:tail], title+table)
	os.WriteFile("README.md", []byte(content), 0644)
}
