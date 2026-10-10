"""GPS-only WebApp contract; no Telegram API or real geolocation required."""
import json
from pathlib import Path
import re
import shutil
import subprocess

import pytest

HTML = Path(__file__).with_name("webapp").joinpath("index.html").read_text()


def test_webapp_has_only_gps_controls():
    assert re.findall(r'<button\b[^>]*id="([^"]+)"', HTML) == ["gps-button"]
    assert not re.search(r"<(?:input|select|option)\b", HTML)
    for removed in ("set_settings", "rano", "wieczor", "hoursTitle", "btnSave",
                    "save-settings-button", "ui-morning", "ui-evening"):
        assert removed not in HTML
    assert "Aktualizuj przez GPS" in HTML


def test_gps_button_colors():
    css = re.search(r"#gps-button\s*\{([^}]+)\}", HTML).group(1)
    assert "background-color: #22c55e;" in css
    assert "color: #000000;" in css


@pytest.mark.parametrize("mode", ["success", "error", "unsupported"])
def test_gps_click_uses_existing_telegram_payload(mode):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js required to execute WebApp JavaScript")
    script = re.findall(r"<script>(.*?)</script>", HTML, re.S)[0]
    harness = r'''
const assert = require('node:assert/strict');
const vm = require('node:vm');
const elements = Object.fromEntries(['ui-title', 'gps-text', 'gps-button'].map(id =>
    [id, {addEventListener(event, fn) {assert.equal(event, 'click'); this.click = fn;}}]));
const sent = [], alerts = [];
let ready = 0, expand = 0;
const tg = {ready() {ready++;}, expand() {expand++;}, sendData(data) {sent.push(JSON.parse(data));}};
const navigator = MODE === 'unsupported' ? {} : {geolocation: {
    getCurrentPosition(success, error) {
        if (MODE === 'error') error({message: 'denied'});
        else success({coords: {latitude: 54.5, longitude: 18.5}});
    }
}};
vm.runInNewContext(SCRIPT, {
    window: {Telegram: {WebApp: tg}, location: {search: '?lang=pl'}},
    document: {getElementById(id) {assert.ok(elements[id], id); return elements[id];}},
    navigator, URLSearchParams, alert(message) {alerts.push(message);}
});
assert.equal(ready, 1);
assert.equal(expand, 1);
assert.equal(elements['gps-text'].innerText, 'Aktualizuj przez GPS');
assert.deepEqual(sent, []);
elements['gps-button'].click();
if (MODE === 'success') {
    assert.deepEqual(sent, [{type: 'set_location', lat: 54.5, lon: 18.5}]);
    assert.deepEqual(alerts, []);
} else {
    assert.deepEqual(sent, []);
    assert.equal(alerts.length, 1);
}
'''
    subprocess.run([node, "-e", "const MODE = " + json.dumps(mode) + ";\n"
                    + "const SCRIPT = " + json.dumps(script) + ";\n" + harness],
                   check=True, capture_output=True, text=True)
