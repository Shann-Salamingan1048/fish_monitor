"""Test the actual input sketch with unset/valid/invalid code constants.

Temporary copies use synthetic ADC data; the distributed sketch is never edited.
Requires a development C++17 compiler, not needed on the deployed Raspberry Pi.
"""
from pathlib import Path
import subprocess
import tempfile

root = Path(__file__).resolve().parents[1]
sketch = root / 'arduino/Nano_INPUT_Millis/Nano_INPUT_Millis.ino'
text = sketch.read_text()
assert '#include <EEPROM.h>' not in text
assert 'EEPROM.' not in text
assert 'PHCAL:START' not in text
with tempfile.TemporaryDirectory() as directory:
    work = Path(directory)
    for label, low, high in [('unset', '0.0f', '0.0f'), ('measured_fixture', '900.0f', '800.0f'),
                             ('equal', '900.0f', '900.0f'), ('reversed', '800.0f', '900.0f'),
                             ('rail', '1023.0f', '800.0f')]:
        copy = work / 'input.ino'
        copy.write_text(text.replace('PH_ADC_686 = 0.0f;', f'PH_ADC_686 = {low};')
                            .replace('PH_ADC_918 = 0.0f;', f'PH_ADC_918 = {high};'))
        binary = work / label
        subprocess.run(['g++', '-std=c++17', '-I', str(root/'tests/firmware_stubs'),
                        f'-DAQUAPI_INPUT_SKETCH="{copy}"', str(root/'tests/input_firmware_test.cpp'),
                        '-o', str(binary)], check=True)
        subprocess.run([str(binary)], check=True)
        print(f'{label}: passed', flush=True)
