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

把 `zfovik-32888` 分支推送到自己的 KernelSU fork，并向该 fork 的 `main` 分支开 PR。PR 会触发完整配对构建：LKM、ksud/ksuinit、Manager APK 共用同一临时签名证书。不要向上游 `tiann/KernelSU` 发 PR。

本次 PR 的临时签名每次都会变化，APK 仅用于并装测试；后续重新构建的 PR APK 不能直接覆盖旧 PR APK。构建结果包含：

```text
ZFovikRT-KernelSU-32888-android17-6.18  # LKM + source/hash metadata
manager                              # 配套 Manager APK
```

## 验证

```powershell
python .\scripts\verify_lkm_version.py .\dist\kernelsu_32888_android17-6.18.ko --expected-version 32888
```

校验程序从 ELF 的 `do_get_info` 和 `do_get_info_legacy` 两个函数读取实际编译后的版本常量，同时核对 ARM64、6.18 vermagic 并计算 SHA-256。校验成功不代表目标平板 ABI 或运行稳定性已通过。

配套 Manager 通过同源 UAPI 头文件报告 UAPI 8，APK `versionCode` 为 32888，包名为 `me.weishu.kernelsu.pr`。此前 32725 定制版补丁不能直接套到本基线。

## 本地准备记录

2026-11-15：已确认官方成功构建 `43599961688` 的 ARM64 6.18 模块实际报告版本 32730，并用真实 32725 / 32730 模块验证校验脚本的正向和错误版本拒绝逻辑。源码改动、JSON、workflow YAML 和 PowerShell 语法检查通过。GitHub CLI 尚未登录；PR 构建将输出同一临时证书签名的 LKM 与 Manager APK。尚未触发构建，因此还没有配套成品。

## 当前限制

源码和 workflow 就绪不等于已经获得 `.ko` 成品；必须有一次实际成功的 DDK 编译和产物校验。设备部署另行进行，不能在当前运行的 KSU 上直接卸载重载来代替重启测试。
