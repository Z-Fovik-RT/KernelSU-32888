# Z-Fovik-RT KernelSU LKM 32888

这是按用户要求将实际 LKM 版本接口固定为 `32888` 的源码构建方案。

- 上游基线：`tiann/KernelSU@64833c005eca6a8dc3d73ec2d37d84c37e0d9ad2`，官方构建版本为 32730。
- 目标：ARM64 / `android17-6.18`，使用官方同系列 DDK 容器构建。
- 内核源码只修改 `kernel/Kbuild` 的版本计算；管理器认证、Root 实现和 Seccomp 实现沿用该上游提交。
- `32888` 是本地定制编号，不是官方发布号，也不表示包含未来上游提交。
- 不使用本机 WSL，不向平板推送或加载模块。

## 构建

在 PowerShell 中运行：

```powershell
gh auth login --hostname github.com --git-protocol https --scopes workflow --web
pwsh -NoProfile -File .\custom\build-on-github.ps1
```

脚本会检查 GitHub CLI 登录状态，使用当前账号的 `KernelSU-32888` 专用 fork，推送 `zfovik-32888` 分支，启用并触发 `Build Z-Fovik-RT LKM 32888`，等待完成后下载产物并在 Windows 上复核模块版本。

构建结果：

```text
dist/
  kernelsu_32888_android17-6.18.ko
  BUILD_INFO.json
  SOURCE_CONFIG.json
  SHA256SUMS.txt
  ELF_INFO.txt
  build.log
```

也可在 fork 的 Actions 页面选择上述 workflow，手动点击 Run workflow。

## 验证

```powershell
python .\scripts\verify_lkm_version.py .\dist\kernelsu_32888_android17-6.18.ko --expected-version 32888
```

校验程序从 ELF 的 `do_get_info` 和 `do_get_info_legacy` 两个函数读取实际编译后的版本常量，同时核对 ARM64、6.18 vermagic 并计算 SHA-256。校验成功不代表目标平板 ABI 或运行稳定性已通过。

管理器保留上游官方包名和证书认证配置。管理器选择以该源码提交对应的 UAPI 为依据；不需要为了显示 32888 而伪造管理器版本。此前 32725 定制版补丁不能直接套到本基线。

## 本地准备记录

2026-11-15：已确认官方成功构建 `43599961688` 的 ARM64 6.18 模块实际报告版本 32730，并用真实 32725 / 32730 模块验证校验脚本的正向和错误版本拒绝逻辑。源码改动、JSON、workflow YAML 和 PowerShell 语法检查通过。当前机器 GitHub CLI 未登录，也没有可用的 Docker / Podman；尚未触发自定义云端编译，因此还没有 32888 `.ko` 成品。

## 当前限制

源码和 workflow 就绪不等于已经获得 `.ko` 成品；必须有一次实际成功的 DDK 编译和产物校验。设备部署另行进行，不能在当前运行的 KSU 上直接卸载重载来代替重启测试。
