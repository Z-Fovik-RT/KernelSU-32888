# Z-Fovik-RT KernelSU LKM 32888

这是按用户要求将实际 LKM 版本接口固定为 `32888` 的源码构建方案。

- 上游基线：`tiann/KernelSU@64833c005eca6a8dc3d73ec2d37d84c37e0d9ad2`，官方构建版本为 32730。
- 目标：ARM64 / `android17-6.18`，使用官方同系列 DDK 容器构建。
- LKM 报告版本固定为 `32888`，Manager APK 的 `versionCode` 也固定为 `32888`，`versionName` 为 `v3.3.1-32888-zfovik`。
- 共享 UAPI 标记改为 `8`，Manager JNI、内核和 ksud 都从同一个 UAPI 头文件构建；当前没有增加新的 IOCTL 或结构体字段。
- Manager 使用 `me.weishu.kernelsu.pr` 包名和 PR 临时证书，与同一 Actions run 编译的 LKM 配对。PR APK 可与现有正式 Manager 并装，不会覆盖现有数据。
- `32888` 是本地定制编号，不是官方发布号，也不表示包含未来上游提交。
- 不使用本机 WSL，不向平板推送或加载模块。

## 构建

本地 `zfovik-32888` 分支对应 fork 的 `build-32888-uapi8` 分支，已向自己的 `main` 建立 PR #1。PR 流程编译 LKM、ksud/ksuinit 和 Manager APK；Manager 由本次临时证书签名，LKM 内嵌同一证书指纹用于认证 Manager。LKM 本身不是用 APK 证书签名。不要向上游发定制版本 PR。

本次 PR 的临时签名每次都会变化，APK 仅用于并装测试；后续重新构建的 PR APK 不能直接覆盖旧 PR APK。构建结果包含：

```text
ZFovikRT-KernelSU-32888-pair  # LKM, Manager APK, hashes and verification reports
manager                     # 配套 Manager APK
```

## 验证

```powershell
python .\scripts\verify_lkm_version.py .\dist\kernelsu_32888_android17-6.18.ko --expected-version 32888
```

校验程序从 ELF 的 `do_get_info` 和 `do_get_info_legacy` 两个函数读取实际编译后的版本常量，同时核对 ARM64、6.18 vermagic 并计算 SHA-256。校验成功不代表目标平板 ABI 或运行稳定性已通过。

配套 Manager 通过同源 UAPI 头文件报告 UAPI 8，APK `versionCode` 为 32888，包名为 `me.weishu.kernelsu.pr`。此前 32725 定制版补丁不能直接套到本基线。

## 本地准备记录

2026-11-15：PR #1 的运行 `43696772809` 已全部成功，分支提交为 `fcf823cbfa2eabd2ed76c7b50f16295874706349`。实际 PR 合并构建提交为 `0b24a7c7bfc11423f53adc262fbffe506d767edf`。构建产物已下载到 `artifacts/run-43696772809/pair/`。

已核验 LKM 的 GET_INFO 与兼容接口版本 32888、UAPI 8；APK manifest 的 versionCode 32888，以及 ARM64 JNI 的 UAPI 8 返回值。APK 签名指纹与构建时嵌入 LKM 的预期指纹一致。两次先前失败分别是检查器只识别 STP 栈写入、以及只识别一种 apksigner 标签，均已修复并在真实产物上验证。

一键 Root 32888 专用包在相邻的 `xring-zfovik-32888` 目录，本地打包脚本引用原签名密钥，不上传密钥。它内嵌本次 LKM，并已全包检查 32725 字样零残留。

## 当前限制

本次完成了真实 DDK 构建、APK 打包和静态校验，未在目标平板上安装、加载或触发 Root；设备 ABI、Seccomp 状态和稳定性仍需真机验证。
