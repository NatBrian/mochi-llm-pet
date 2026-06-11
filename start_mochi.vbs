Set WshShell = CreateObject("WScript.Shell")
WshShell.CurrentDirectory = "C:\Users\\mochi-llm-pet"
WshShell.Run """C:\Users\AppData\Local\miniconda3\envs\mochipet\python.exe"" run.py", 7, False