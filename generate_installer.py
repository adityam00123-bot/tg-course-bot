import base64
from pathlib import Path

zip_path = Path("telegram_migrator_cloud.zip")
if not zip_path.exists():
    print("Zip does not exist")
    exit(1)

data = zip_path.read_bytes()
b64 = base64.b64encode(data).decode("ascii")

script_content = f"""#!/bin/bash
cat << 'EOF' | base64 -d > telegram_migrator_cloud.zip
{b64}
EOF

unzip -o telegram_migrator_cloud.zip -d bot
cd bot
chmod +x start_cloud.sh
./start_cloud.sh
"""

Path("cloud_install.sh").write_text(script_content, encoding="utf-8")
print(f"Generated cloud_install.sh successfully! Size: {len(script_content)} bytes")
