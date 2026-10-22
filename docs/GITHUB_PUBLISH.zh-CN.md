# 首次发布这个基线

仓库名使用 `Mules-Supply-Mod-OPUS`。当前目录是从已验证的安装内容导出的源码，只有一个基线提交，不包含开发历史、旧归档、存档、诊断转储或备份。

## 在 GitHub 建空仓库

1. 登录 https://github.com/new 。
2. Repository name 填 `Mules-Supply-Mod-OPUS`。
3. 自行选择 Public 或 Private。
4. **不要勾选**初始化 README、`.gitignore` 或 License：这些文件已经包含在本地基线中；另选模板许可证还可能覆盖原有 CC BY 4.0。
5. 创建后保留仓库网址，例如 `https://github.com/你的用户名/Mules-Supply-Mod-OPUS`。

## 让本机能够推送

安装 GitHub CLI：https://cli.github.com/ 。安装后新开终端，并执行：

```powershell
gh --version
gh auth login
gh auth status
```

登录时选择 GitHub.com、HTTPS、浏览器授权即可。账号密码、浏览器验证码和访问令牌直接在 GitHub/CLI 自己的界面输入，不需要粘贴到聊天中。

将创建好的仓库 URL 发给协助发布的代理。它可以核对当前这一个基线提交、设置 origin，再将它推到空仓库 main。首次建立仓库无需为了同一个初始快照额外创建 PR；之后功能改动可以走分支和 PR。

## 自行推送

在此仓库根目录执行，URL 换成自己的真实地址：

```powershell
git remote add origin https://github.com/你的用户名/Mules-Supply-Mod-OPUS.git
git push -u origin main
```

若该目录尚未有提交，先在确认文件范围后创建初始提交；不要对整个 X4 游戏目录执行 `git add`。GitHub 的分支历史应从本项目的一个基线快照开始，而不是从开发期间的备份目录导入。

需要可安装包时，执行 `python tools/build.py`，将生成的 `dist/Mules-Supply-Mod-OPUS-baseline.zip` 作为 Release 附件上传。`dist/` 默认不提交，源码仓库始终保持可读、可比较的 XML。
