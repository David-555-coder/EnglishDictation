# EnglishDictation · 英文文章智能默写

**用自己的 Word 文章，在完整语境中默写英文，再围绕错词原句持续复习。**

EnglishDictation 是一款本地优先的 Windows 学习工具：保留文章上下文和中文提示，将重点词变成填空；填写期间不提前判分，交卷后统一核对，再把薄弱词带入后续复习。

[下载 Windows 免安装版](https://github.com/David-555-coder/EnglishDictation/releases/latest) · [详细使用说明](docs/USER_GUIDE.md) · [反馈问题](https://github.com/David-555-coder/EnglishDictation/issues)

## 从文章到复习

1. **导入自己的文章**：选择 Word 文件，软件识别重点英文及其中文释义。
2. **先读，再回忆**：检查原文与挖空位置，在保留上下文的文章中填写英文。
3. **主动交卷**：填写完成后统一批改，查看正确答案和本轮错词。
4. **回到错词原句**：阅读薄弱词所在的句子，按需再练；通过今日复习继续巩固。

例如，原句中的 `momentum` 写错后，下一轮可以只练这一句：

```text
The extensive research gained ________（势头）.
```

## 主要功能

| 功能 | 使用方式 |
|---|---|
| Word 文章导入 | 支持批量导入 `.docx`；旧式 `.doc` 通过本机 Microsoft Word 转换 |
| 重点词挖空 | 提供标准、宽松、严格三种规则，可手动添加、取消、合并或拆分题目 |
| 学习内容编辑 | 按具体句段修正本句释义、正文和易混备注，保留原 Word 与历史成绩 |
| 整篇默写与统一批改 | 输入时不提前显示对错，主动交卷后核对原词回忆结果 |
| 错词原句练习 | 从本轮结果、历史记录或错词本进入，保留句子上下文 |
| 今日复习 | 使用 FSRS-7 记忆模型，结合每日总量安排持续复习与逐批加入的旧词 |
| 阅读与查词 | 连续拖选中英文、复制、缩放；阅读时右键查看本地英汉词典 |
| 草稿与学习备份 | 自动保存输入，手动继续未完成练习，支持导出与恢复学习备份 |
| 打印与导出 | 导出 Word 默写纸或答案版，方便纸上练习 |
| 快速成文 | 复制可自定义的分步提示词，供你在第三方 AI 中生成文章后手动导入 |

## 下载与启动

适用于 **Windows 64 位**。免安装版无需 Python，也无需注册账号。

1. 打开 [Releases](https://github.com/David-555-coder/EnglishDictation/releases/latest)，下载 `EnglishDictation-…-Windows-x64.zip` 软件附件。
2. **完整解压**到可写的普通文件夹，保留 EXE 同级的 `assets` 文件夹。
3. 双击 `EnglishDictation.exe`，从“导入”选择自己的 Word 文章。

解压后的主要文件：

```text
EnglishDictation/
├─ EnglishDictation.exe
├─ assets/
│  ├─ dictionary.sqlite3
│  ├─ dictionary_supplements.json
│  ├─ ECDICT-LICENSE.txt
│  └─ FSRS-LICENSE.txt
└─ 使用说明.txt
```

`assets` 是软件的公共资源，不是个人资料。缺少词库时仍可启动学习功能，但本地查词不可用。首次使用不附带文章或学习记录，需要自行导入。

### 怎样准备 Word 文章

默认“标准模式”识别：**红色或加粗的英文，后面紧邻中文释义**。例如，将 Word 中的 `momentum` 设为红色或加粗，后接 `（势头）`。标题样式默认不挖空。

导入后先查看原文，确认识别结果；不合适的地方可通过“文章与设置 → 编辑学习内容”调整。文章内容、中文提示和原始 Word 由你掌握。

## 数据保存与版本更新

学习数据默认保存在程序同级的 `data` 文件夹，包含导入文章副本、草稿、错词、成绩和设置。若程序目录不可写，则使用 `%LOCALAPPDATA%\EnglishDictation`。日志保存在对应位置的 `logs` 中。

**从旧版更新：**

1. 关闭软件，先通过“导出 → 导出学习备份…”保存备份。
2. 解压新版，将 `EnglishDictation.exe` 和随包提供的 `assets` 更新到原软件目录。
3. **保留原来的 `data`，不要用空目录或别人的数据覆盖它。**

新版需要的数据库升级会自动进行，并保留迁移备份。更换电脑时请连同 `data` 一起复制，或使用软件内的备份与恢复功能。旧程序不保证能读取新版数据；回退前请保留升级前备份。

仓库和 Release 只提供软件及公共资源，不包含开发者的个人文章、学习数据库、备份或日志。本地这些内容由 `.gitignore` 排除。

## 离线使用与联网范围

文章学习、判题、复习安排和本地查词均可离线使用，无需 API 密钥。

只有主动点击查词卡片里的“联网参考”，并同意首次说明后，才会向 MyMemory 发送必要的所选英文及原句。软件不自动上传整篇文章或学习数据库。在线结果可能误译，本地词典也不会自动判断某个词在句中的唯一词义。

“快速成文”只复制提示词，不会自动调用 AI。是否向第三方 AI 提交词表、使用哪个服务，由你自行决定。

## 常用快捷键

| 操作 | 快捷键 |
|---|---|
| 导入文章 | `Ctrl+O` |
| 查看原文 / 开始默写 | `Ctrl+1` / `Ctrl+2` |
| 下一个 / 上一个输入框 | `Enter` / `Shift+Enter` |
| 清空当前答案 | `Shift+Backspace` |
| 提交批改 | `Ctrl+Enter` |
| 专注模式 | `F8` |
| 查看完整快捷键说明 | `F1` |

## 源码运行与开发

使用 Python 3.11 或以上版本，在 Windows 项目目录执行：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe app.py
```

开发测试与打包：

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
$env:QT_QPA_PLATFORM = "offscreen"
.\.venv\Scripts\python.exe -m pytest -q
Remove-Item Env:QT_QPA_PLATFORM
.\.venv\Scripts\python.exe -m PyInstaller --noconfirm EnglishDictation.spec
```

打包结果在 `dist`；分发时必须同时保留 EXE 与生成的 `assets`。正式学习数据不要作为测试输入，界面验证应使用隔离的临时文章库。

## 使用边界与反馈

- 当前以 Word 文章为主，不支持 PDF/OCR；复杂表格、文本框和修订内容可能无法完整还原。
- 判分按指定英文原词比较，不自动接受所有近义表达。FSRS 的目标记忆率是模型设置，不是学习效果保证。
- 当前为免安装版，没有自动更新功能。
- 本地词库基于 [ECDICT](https://github.com/skywind3000/ECDICT)，许可见 [ECDICT-LICENSE.txt](assets/ECDICT-LICENSE.txt)；FSRS 推理核心的上游许可见 [FSRS-LICENSE.txt](assets/FSRS-LICENSE.txt)。

遇到问题时，请在 [Issues](https://github.com/David-555-coder/EnglishDictation/issues) 中说明版本、操作步骤和预期结果。可提供去掉个人信息的截图或最小 Word 样例，无需公开整个文章库或学习数据库。
