# ChaturbateRecorder

自动记录 chaturbate.com 上公开网络摄像机表演的脚本。

我在 Debian(7+8)、Ubuntu 14、freenas10和 Mac OS X(10.10.4) 上进行了测试，但它应该能在其他操作系统上运行。
我没有 Windows 机器可以测试，但我让另一个用户在 Windows 上测试，他报告说在 Windows 10 上运行 Python3.6.2 的 6/21/17 更新是有效的(也可能在 Python 3.5+ 上工作)。

## 要求

需要使用Python3.5或更高版本。您可以从https://www.python.org/ downloads / release / python-352 /下载Python3.5.2。

要安装所需的模块，请运行：
```
python3.5 -m pip install streamlink bs4 lxml gevent
```


编辑配置文件（config.conf），使其指向你想要录制的目录，该目录中的“wanted”文件所在位置，指定性别以及检查的时间间隔（以秒为单位）。
在“wanted.txt”文件中添加模特（每行只能添加一个模特）。模特的名称应与其聊天室URL中的名称匹配（https://chaturbate.com/{modelname}/）。请注意，该名称应仅为“modelname”部分，而不是整个URL。
