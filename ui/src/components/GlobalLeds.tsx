import type { LedConfig, LedMode } from '../types';
import { ColorPicker } from './ColorPicker';
import { rgbToHex } from '../utils';

interface Props {
  leds:     LedConfig;
  onChange: (leds: LedConfig) => void;
}

export function GlobalLeds({ leds, onChange }: Props) {
  const update = (patch: Partial<LedConfig>) => onChange({ ...leds, ...patch });
  const lowHex  = rgbToHex(leds.low_color);
  const highHex = rgbToHex(leds.high_color);

  const previewBg =
    leds.mode === 'off'    ? '#000' :
    leds.mode === 'static' ? highHex :
    `linear-gradient(to right, ${lowHex}, ${highHex})`;

  return (
    <section className="card" id="global-leds">
      <h2>Global LEDs</h2>
      <div className="fields">
        <div className="field">
          <label htmlFor="led-mode">Mode</label>
          <select
            id="led-mode"
            value={leds.mode}
            onChange={(e) => update({ mode: e.target.value as LedMode })}
          >
            <option value="volume">volume — fade low → high</option>
            <option value="static">static — always high color</option>
            <option value="off">off — LEDs disabled</option>
          </select>
        </div>

        <div className="field" style={{ opacity: leds.mode === 'volume' ? 1 : 0.4 }}>
          <ColorPicker
            id="led-low-color"
            label="Low color (0 %)"
            value={leds.low_color}
            onChange={(low_color) => update({ low_color })}
          />
        </div>

        <div className="field">
          <ColorPicker
            id="led-high-color"
            label="High color (100 %)"
            value={leds.high_color}
            onChange={(high_color) => update({ high_color })}
          />
        </div>
      </div>

      <div className="led-preview" style={{ background: previewBg }} />
    </section>
  );
}
