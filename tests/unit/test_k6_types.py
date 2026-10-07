"""Validate the JS boundary behavior and portable generated type contracts."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from tools.bench import commands
from tools.bench.engine.resources import wrapper_path

ROOT = Path(__file__).resolve().parents[2]
BUN = shutil.which("bun")
pytestmark = pytest.mark.skipif(BUN is None, reason="requires locked Bun development tools")


def test_json_boundaries_reject_malformed_records():
    result = subprocess.run(
        [
            "bun",
            "-e",
            """
import assert from 'node:assert/strict';
import {actors, pairs, journeyConfig, record} from './benchmarks/k6/lib/data.js';
import {engineConfig, stageAt} from './benchmarks/k6/engine/metrics.js';
assert.throws(() => record(null));
assert.throws(() => actors({actors: [{username: 'a', password: 1}]}));
assert.throws(() => pairs({like_pairs: [[0, '1']]}));
assert.throws(() => journeyConfig({users: '2'}));
assert.deepEqual(actors({actors: [{username: 'a', password: 'b'}]})[0].username, 'a');
assert.deepEqual(pairs({like_pairs: [[0, 1]]}), [[0, 1]]);
const stage = {id: 'hold', duration: 1, start_seconds: 0, end_seconds: 1,
               from_users: 2, to_users: 2, evaluate: true};
const config = engineConfig({baseUrl: 'http://localhost', stages: [stage]});
assert.equal(stageAt(config.stages, 0.5), stage);
assert.equal(stageAt(config.stages, 1), null);
assert.throws(() => engineConfig({baseUrl: 'x', stages: [{...stage, duration: '1'}]}));
assert.throws(() => engineConfig({baseUrl: 'x', stages: [stage], parameters: {x: Infinity}}));
""",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("template", ["http", "social"])
def test_generated_template_types_resolve_outside_checkout(tmp_path, template):
    assert (
        commands.main(["init", "--template", template, "--output", str(tmp_path / "job.yaml")]) == 0
    )
    assets = tmp_path / "job-assets"
    assert (assets / "contracts.d.ts").read_bytes() == wrapper_path("contracts.d.ts").read_bytes()
    config = {
        "compilerOptions": {
            "strict": True,
            "allowJs": True,
            "checkJs": True,
            "noEmit": True,
            "target": "ES2022",
            "module": "ESNext",
            "moduleResolution": "Bundler",
            "lib": ["ES2022"],
            "types": ["k6"],
            "typeRoots": [str(ROOT / "node_modules/@types")],
        },
        "include": ["job-assets/journey.js"],
    }
    (tmp_path / "tsconfig.json").write_text(json.dumps(config))
    result = subprocess.run(
        ["bun", str(ROOT / "node_modules/typescript/bin/tsc"), "-p", str(tmp_path)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
