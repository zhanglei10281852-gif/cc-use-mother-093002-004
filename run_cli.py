import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))
from skill_recognition.contracts import StandardVersion, EvidenceRecord

entity = StandardVersion("E-DEMO", "中文职业能力互认", 1)
record = EvidenceRecord("R-DEMO", entity.entity_id, "已登记")
print(json.dumps({"entity": entity.display_name, "revision": entity.revision, "record_state": record.category}, ensure_ascii=False))
