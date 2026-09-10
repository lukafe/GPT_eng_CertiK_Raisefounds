import sys
from pathlib import Path

# Permite `import common`, `import llama` etc. de dentro de tests/.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
