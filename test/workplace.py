from pathlib import Path
import os

print(os.name)
print(Path(__file__))
print(Path(__file__).resolve().parent.parent)
print(Path(__file__).relative_to(Path(__file__).resolve().parent.parent))
# print(Path(__file__).resolve())

# print(Path(__file__).resolve().parent.parent)

# print(True if Path(__file__).resolve()  not in Path(__file__).resolve().parent else False)
# print(*Path.cwd().parents)
