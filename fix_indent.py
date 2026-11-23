import pathlib
p = pathlib.Path('F:/Codex Xiangm/amd-notebook/breezeblue-manager/app/main.py')
content = p.read_text(encoding='utf-8')
lines = content.split(chr(10))
fixed = [l[1:] if l.startswith(' ') else l for l in lines]
p.write_text(chr(10).join(fixed), encoding='utf-8')
print('fixed')
