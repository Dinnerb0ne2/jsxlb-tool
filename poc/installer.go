// 构建: go build -ldflags "-H=windowsgui" -o installer.exe installer.go —— 证明 electron-updater 会静默执行服务器下发的任意 exe。
// 链路: app-update.yml 无 publisherName(Authenticode 校验跳过)
//      + 客户端内置 Ed25519 验签模块 utils/update-verifier.js 从未被调用(死代码)
//      + NODE_TLS_REJECT_UNAUTHORIZED='0'
//
// 构建: go build -ldflags "-H=windowsgui" -o installer.exe installer.go
package main

import (
	"fmt"
	"syscall"
	"unsafe"
)

func alert(text string) {
	user32 := syscall.NewLazyDLL("user32.dll")
	mb := user32.NewProc("MessageBoxW")
	s, _ := syscall.UTF16PtrFromString(text)
	mb.Call(0, uintptr(unsafe.Pointer(s)), 0, 0)
}

func main() {
	alert("PoC: 教室小喇叭伪更新 RCE 成功\n任意代码已在本机执行 (当前用户权限)")
	fmt.Println("pwned")
}
