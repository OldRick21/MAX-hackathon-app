"""Run isolated backend/service checks with dependencies installed in .test-deps."""
import os
from pathlib import Path
import subprocess
import sys

root = Path(__file__).resolve().parents[1]
env = os.environ.copy()
env['PYTHONPATH'] = os.pathsep.join([str(root / '.test-deps'), str(root / 'backend')])
suites = {
    'privacy': ('backend', 'tests.test_privacy'),
    'max': ('backend', 'tests.test_max_integration'),
    'jwt': ('backend', 'tests.test_jwt_contract'),
    'operator': ('backend', 'tests.test_operator_panel'),
    'join': ('backend', 'tests.test_join_requests'),
    'profile': ('services/user-profile', 'tests.test_profiles'),
    'profile-integration': ('services/user-profile', 'tests.test_integration_core'),
    'schedule': ('services/schedule', 'tests.test_schedule'),
    'schedule-integration': ('services/schedule', 'tests.test_integration_core'),
    'coursework': ('test-data/coursework', 'tests.test_coursework'),
    'coursework-integration': ('test-data/coursework', 'tests.test_integration_core'),
    'protocol': ('backend', 'tests.test_privacy_protocol'),
}
failed = []
for name in sys.argv[1:] or ['privacy', 'max', 'jwt', 'operator', 'join', 'protocol']:
    cwd, module = suites[name]
    print(f'Running {name}', flush=True)
    result = subprocess.run([sys.executable, '-m', 'unittest', module, '-v'], cwd=root / cwd, env=env)
    if result.returncode:
        failed.append(name)
print('Failed: ' + ', '.join(failed) if failed else 'All selected suites passed', flush=True)
sys.exit(bool(failed))
