Set WshShell = CreateObject("WScript.Shell")
WshShell.CurrentDirectory = "C:\Users\\mochi-llm-pet"
WshShell.Environment("Process").Item("DESKPET_MANIFEST") = "anim_manifest.yaml"
WshShell.Environment("Process").Item("DESKPET_PERSONA") = "ginger"
WshShell.Run """C:\Users\AppData\Local\miniconda3\envs\mochipet\python.exe"" run.py --config ""C:\Users\\mochi-llm-pet\config_ginger.toml""", 7, False