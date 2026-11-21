"""Use the deployed report consumer's canonical identities in the baseline gate."""
from pathlib import Path
import yaml

SOURCE = Path(__file__).resolve().parents[2] / 'infra/kyverno/automation/github-issue-sync-configmap.yaml'
helper = {'__name__': 'policy_identity'}
exec(compile(yaml.safe_load(SOURCE.read_text())['data']['sync.py'], str(SOURCE), 'exec'), helper)
canonical_identity = helper['canonical_identity']
