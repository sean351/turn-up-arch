import type { Config } from '../types';

interface Props {
  config:   Config;
  onChange: (patch: Partial<Pick<Config, 'port' | 'baud'>>) => void;
}

export function Connection({ config, onChange }: Props) {
  return (
    <section className="card" id="connection">
      <h2>Connection</h2>
      <div className="fields">
        <div className="field">
          <label htmlFor="port">Serial port</label>
          <input
            type="text"
            id="port"
            value={config.port}
            placeholder="/dev/ttyACM0"
            onChange={(e) => onChange({ port: e.target.value })}
          />
        </div>
        <div className="field">
          <label htmlFor="baud">Baud rate</label>
          <input
            type="number"
            id="baud"
            value={config.baud}
            onChange={(e) =>
              onChange({ baud: parseInt(e.target.value, 10) || 115200 })
            }
          />
        </div>
      </div>
    </section>
  );
}
