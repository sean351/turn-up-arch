import { useState } from 'react';
import type { Config, ToastItem } from '../types';
import * as api from '../api';

interface Props {
  presets:  string[];
  config:   Config;
  onLoad:   (cfg: Config) => void;
  onRefresh: () => void;
  onToast:  (msg: string, type?: ToastItem['type']) => void;
}

export function Presets({ presets, config, onLoad, onRefresh, onToast }: Props) {
  const [name, setName] = useState('');

  const handleSave = async () => {
    const trimmed = name.trim();
    if (!trimmed) { onToast('Enter a preset name first', 'info'); return; }
    try {
      await api.savePreset(trimmed, config);
      onToast(`Preset "${trimmed}" saved`);
      setName('');
      onRefresh();
    } catch (err) {
      onToast(`Save preset failed: ${(err as Error).message}`, 'error');
    }
  };

  const handleLoad = async (presetName: string) => {
    try {
      const cfg = await api.fetchPreset(presetName);
      onLoad(cfg);
      onToast(`Preset "${presetName}" loaded — click Save Config to write to disk`, 'info');
    } catch (err) {
      onToast(`Load failed: ${(err as Error).message}`, 'error');
    }
  };

  const handleApply = async (presetName: string) => {
    try {
      await api.applyPreset(presetName);
      const cfg = await api.fetchConfig();
      onLoad(cfg);
      onToast(`Preset "${presetName}" applied — daemon will reload automatically`);
    } catch (err) {
      onToast(`Apply failed: ${(err as Error).message}`, 'error');
    }
  };

  const handleDelete = async (presetName: string) => {
    if (!confirm(`Delete preset "${presetName}"?`)) return;
    try {
      await api.deletePreset(presetName);
      onToast(`Preset "${presetName}" deleted`);
      onRefresh();
    } catch (err) {
      onToast(`Delete failed: ${(err as Error).message}`, 'error');
    }
  };

  return (
    <section className="card" id="presets">
      <h2>Presets</h2>

      <div className="save-row">
        <div className="field">
          <label htmlFor="preset-name">Preset name</label>
          <input
            type="text"
            id="preset-name"
            value={name}
            placeholder="my-setup"
            onChange={(e) => setName(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && void handleSave()}
          />
        </div>
        <button className="btn-secondary" onClick={() => void handleSave()}>
          Save current as preset
        </button>
      </div>

      <div className="preset-list">
        {presets.length === 0 ? (
          <p className="empty">No presets saved yet.</p>
        ) : (
          presets.map((p) => (
            <div key={p} className="preset-item">
              <span className="preset-name">{p}</span>
              <div className="preset-actions">
                <button className="btn-secondary btn-sm" onClick={() => void handleLoad(p)}>
                  Load
                </button>
                <button className="btn-primary btn-sm" onClick={() => void handleApply(p)}>
                  Apply
                </button>
                <button className="btn-danger btn-sm" onClick={() => void handleDelete(p)}>
                  Delete
                </button>
              </div>
            </div>
          ))
        )}
      </div>
    </section>
  );
}
