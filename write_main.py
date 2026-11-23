import pathlib
content = open('F:/Codex Xiangm/amd-notebook/breezeblue-manager/gen_main_content.py', 'r', encoding='utf-8').read()
pathlib.Path('F:/Codex Xiangm/amd-notebook/breezeblue-manager/app/main.py').write_text(content, encoding='utf-8')
print('written')
