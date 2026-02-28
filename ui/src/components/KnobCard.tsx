import { useState } from 'react';
import type { KnobConfig, KnobAction, LedConfig, LedMode } from '../types';
import { ColorPicker } from './ColorPicker';

const ACTIONS: { value: KnobAction; label: string }[] = [
  { value: 'sink_volume',   label: 'sink_volume — output device' },
  { value: 'source_volume', label: 'source_volume — microphone' },
  { value: 'app_volume',    label: 'app_volume — single app' },
  { value: 'group_volume',  label: 'group_volume — multiple apps' },
];

const DEFAULT_LED: LedConfig = {
  mode:       'volume',
  low_color:  [255, 0, 0],
  high_color: [0, 255, 0],
};

interface Props {
  index:        number;
  knob:         KnobConfig;
  runningApps?: string[];
  onChange:     (knob: KnobConfig) => void;
}

export function KnobCard({ index, knob, runningApps = [], onChange }: Props) {
  const [ledOpen,      setLedOpen]      = useState(!!knob.led);
  const [pickedApp,    setPickedApp]    = useState('');

  const update = (patch: Partial<KnobConfig>) => onChange({ ...knob, ...patch });

  const ledCfg: LedConfig = {
    mode:       knob.led?.mode       ?? DEFAULT_LED.mode,
    low_color:  knob.led?.low_color  ?? DEFAULT_LED.low_color,
    high_color: knob.led?.high_color ?? DEFAULT_LED.high_color,
  };

  const updateLed = (patch: Partial<LedConfig>) =>
    update({ led: { ...ledCfg, ...patch } });

  const toggleLed = () => {
    const next = !ledOpen;
    setLedOpen(next);
    if (!next) {
      // Drop the led key entirely when panel is closed
      const { led: _dropped, ...rest } = knob;
      onChange(rest as KnobConfig);
    }
  };

  const datalistId = `knob-${index}-apps`;

  // Add the selected running app to the group_volume targets list
  const addPickedApp = () => {
    const app = pickedApp.trim();
    if (!app) return;
    const current = knob.targets ?? [];
    if (!current.includes(app)) {
      update({ targets: [...current, app] });
    }
    setPickedApp('');
  };

  return (
    <div className="knob-card">
      <div className="card-title">
        <span className="card-index">{index}</span>
        Knob {index}
      </div>

      {/* Action */}
      <div>
        <label htmlFor={`knob-${index}-action`}>Action</label>
        <select
          id={`knob-${index}-action`}
          value={knob.action}
          onChange={(e) => {
            const action = e.target.value as KnobAction;
            if (action === 'group_volume') {
              update({ action, targets: knob.targets ?? [], target: undefined });
            } else {
              update({ action, target: knob.target ?? 'default', targets: undefined });
            }
          }}
        >
          {ACTIONS.map((a) => (
            <option key={a.value} value={a.value}>{a.label}</option>
          ))}
        </select>
      </div>

      {/* Target / Targets */}
      {knob.action === 'group_volume' ? (
        <div>
          <label htmlFor={`knob-${index}-targets`}>Targets (comma-separated)</label>
          <textarea
            id={`knob-${index}-targets`}
            value={(knob.targets ?? []).join(', ')}
            placeholder="spotify, vlc, brave"
            onChange={(e) =>
              update({
                targets: e.target.value.split(',').map((s) => s.trim()).filter(Boolean),
              })
            }
          />
          {runningApps.length > 0 && (
            <div className="app-picker">
              <select
                value={pickedApp}
                onChange={(e) => setPickedApp(e.target.value)}
                aria-label="Pick a running app"
              >
                <option value="">— running apps —</option>
                {runningApps.map((app) => (
                  <option key={app} value={app}>{app}</option>
                ))}
              </select>
              <button
                type="button"
                className="btn-secondary"
                onClick={addPickedApp}
                disabled={!pickedApp}
              >
                Add
              </button>
            </div>
          )}
        </div>
      ) : (
        <div>
          <label htmlFor={`knob-${index}-target`}>Target</label>
          <input
            type="text"
            id={`knob-${index}-target`}
            list={runningApps.length > 0 ? datalistId : undefined}
            value={knob.target ?? 'default'}
            placeholder="default"
            onChange={(e) => update({ target: e.target.value })}
          />
          {runningApps.length > 0 && (
            <datalist id={datalistId}>
              {runningApps.map((app) => (
                <option key={app} value={app} />
              ))}
            </datalist>
          )}
        </div>
      )}

      {/* LED override toggle */}
      <button className="led-override-toggle" onClick={toggleLed}>
        <span>{ledOpen ? '▾' : '▸'}</span>
        LED override
      </button>

      {/* LED override panel */}
      {ledOpen && (
        <div className="led-override-panel open">
          <div>
            <label htmlFor={`knob-${index}-led-mode`}>Mode</label>
            <select
              id={`knob-${index}-led-mode`}
              value={ledCfg.mode}
              onChange={(e) => updateLed({ mode: e.target.value as LedMode })}
            >
              <option value="volume">volume</option>
              <option value="static">static</option>
              <option value="off">off</option>
            </select>
          </div>
          <ColorPicker
            id={`knob-${index}-led-low`}
            label="Low color"
            value={ledCfg.low_color}
            onChange={(low_color) => updateLed({ low_color })}
          />
          <ColorPicker
            id={`knob-${index}-led-high`}
            label="High color"
            value={ledCfg.high_color}
            onChange={(high_color) => updateLed({ high_color })}
          />
        </div>
      )}
    </div>
  );
}
