import type { ButtonConfig, ButtonAction } from '../types';

const ACTIONS: { value: ButtonAction; label: string }[] = [
  { value: 'mute_sink',   label: 'mute_sink — toggle output mute' },
  { value: 'mute_source', label: 'mute_source — toggle mic mute' },
  { value: 'command',     label: 'command — run shell command' },
];

interface Props {
  index:    number;
  btn:      ButtonConfig;
  onChange: (btn: ButtonConfig) => void;
}

export function ButtonCard({ index, btn, onChange }: Props) {
  const isCmd = btn.action === 'command';

  return (
    <div className="button-card">
      <div className="card-title">
        <span className="card-index">{index}</span>
        Button {index}
      </div>

      <div>
        <label htmlFor={`btn-${index}-action`}>Action</label>
        <select
          id={`btn-${index}-action`}
          value={btn.action}
          onChange={(e) =>
            onChange({ ...btn, action: e.target.value as ButtonAction })
          }
        >
          {ACTIONS.map((a) => (
            <option key={a.value} value={a.value}>{a.label}</option>
          ))}
        </select>
      </div>

      <div>
        <label htmlFor={`btn-${index}-target`}>{isCmd ? 'Command' : 'Target'}</label>
        <input
          type="text"
          id={`btn-${index}-target`}
          value={btn.target}
          placeholder={isCmd ? 'playerctl play-pause' : 'default'}
          onChange={(e) => onChange({ ...btn, target: e.target.value })}
        />
      </div>
    </div>
  );
}
