import { useState } from 'react';
import type { KnobConfig, KnobAction, LedConfig, LedMode, AudioDevice } from '../types';
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
  sinks?:       AudioDevice[];
  sources?:     AudioDevice[];
  onChange:     (knob: KnobConfig) => void;
}

export function KnobCard({ index, knob, runningApps = [], sinks = [], sources = [], onChange }: Props) {
  const [ledOpen, setLedOpen] = useState(!!knob.led);

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
          <label htmlFor={`knob-${index}-targets`}>
            Targets
            {runningApps.length > 0 && (
              <span className="label-hint"> (ctrl/shift to select multiple)</span>
            )}
          </label>
          <select
            id={`knob-${index}-targets`}
            multiple
            size={Math.max(3, Math.min(runningApps.length, 6))}
            value={knob.targets ?? []}
            onChange={(e) =>
              update({
                targets: Array.from(e.target.selectedOptions).map((o) => o.value),
              })
            }
            disabled={runningApps.length === 0}
            aria-label="Targets"
          >
            {runningApps.length === 0 && (
              <option value="" disabled>— no apps detected —</option>
            )}
            {runningApps.map((app) => (
              <option key={app} value={app}>{app}</option>
            ))}
          </select>
        </div>
      ) : (
        <div>
          <label htmlFor={`knob-${index}-target`}>Target</label>
          {knob.action === 'sink_volume' ? (
            <select
              id={`knob-${index}-target`}
              value={knob.target ?? 'default'}
              onChange={(e) => update({ target: e.target.value })}
              disabled={sinks.length === 0}
              aria-label="Target"
            >
              {sinks.length === 0
                ? <option value="default">— no devices detected —</option>
                : sinks.map((d) => (
                    <option key={d.name} value={d.name}>
                      {d.description}{d.is_default ? ' (default)' : ''}
                    </option>
                  ))
              }
            </select>
          ) : knob.action === 'source_volume' ? (
            <select
              id={`knob-${index}-target`}
              value={knob.target ?? 'default'}
              onChange={(e) => update({ target: e.target.value })}
              disabled={sources.length === 0}
              aria-label="Target"
            >
              {sources.length === 0
                ? <option value="default">— no devices detected —</option>
                : sources.map((d) => (
                    <option key={d.name} value={d.name}>
                      {d.description}{d.is_default ? ' (default)' : ''}
                    </option>
                  ))
              }
            </select>
          ) : (
            /* app_volume */
            <select
              id={`knob-${index}-target`}
              value={knob.target ?? 'default'}
              onChange={(e) => update({ target: e.target.value })}
              disabled={runningApps.length === 0}
              aria-label="Target"
            >
              <option value="default">
                {runningApps.length === 0 ? '— no apps detected —' : '— select an app —'}
              </option>
              {runningApps.map((app) => (
                <option key={app} value={app}>{app}</option>
              ))}
            </select>
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
