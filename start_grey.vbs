Set WshShell = CreateObject("WScript.Shell")
WshShell.CurrentDirectory = "C:\Users\\mochi-llm-pet"
WshShell.Environment("Process").Item("DESKPET_MANIFEST") = "anim_manifest_grey.yaml"
WshShell.Environment("Process").Item("DESKPET_PERSONA") = "grey"
WshShell.Run """C:\Users\AppData\Local\miniconda3\envs\mochipet\python.exe"" run.py --config ""C:\Users\\mochi-llm-pet\config_grey.toml""", 7, False