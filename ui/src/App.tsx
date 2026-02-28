import { useState, useEffect, useCallback } from 'react';
import type { Config, ToastItem } from './types';
import * as api from './api';
import { ToastContainer } from './components/Toast';
import { Connection } from './components/Connection';
import { GlobalLeds } from './components/GlobalLeds';
import { KnobCard } from './components/KnobCard';
import { ButtonCard } from './components/ButtonCard';
import { Presets } from './components/Presets';

const KNOB_INDICES = ['0', '1', '2', '3', '4'] as const;
const BTN_INDICES  = ['0', '1', '2', '3', '4'] as const;

let toastSeq = 0;

export default function App() {
  const [config,  setConfig]  = useState<Config | null>(null);
  const [saved,   setSaved]   = useState<Config | null>(null);
  const [presets, setPresets] = useState<string[]>([]);
  const [toasts,  setToasts]  = useState<ToastItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [saving,  setSaving]  = useState(false);

  const addToast = useCallback((message: string, type: ToastItem['type'] = 'success') => {
    const id = ++toastSeq;
    setToasts((prev) => [...prev, { id, message, type }]);
  }, []);

  const removeToast = useCallback((id: number) => {
    setToasts((prev) => prev.filter((t) => t.id !== id));
  }, []);

  const loadPresets = useCallback(async () => {
    try {
      const names = await api.listPresets();
      setPresets(names);
    } catch {
      // Non-fatal — presets dir may not exist yet
    }
  }, []);

  // Initial load
  useEffect(() => {
    void (async () => {
      try {
        const cfg = await api.fetchConfig();
        setConfig(cfg);
        setSaved(cfg);
      } catch (err) {
        addToast(`Failed to load config: ${(err as Error).message}`, 'error');
      } finally {
        setLoading(false);
      }
      await loadPresets();
    })();
  }, [addToast, loadPresets]);

  const isDirty = config !== null && JSON.stringify(config) !== JSON.stringify(saved);

  const handleSave = async () => {
    if (!config) return;
    setSaving(true);
    try {
      await api.saveConfig(config);
      setSaved(config);
      addToast('Config saved — daemon will reload automatically');
    } catch (err) {
      addToast(`Save failed: ${(err as Error).message}`, 'error');
    } finally {
      setSaving(false);
    }
  };

  const handleRevert = () => {
    if (saved) setConfig(saved);
  };

  const patchConfig = useCallback(<K extends keyof Config>(key: K, value: Config[K]) => {
    setConfig((prev) => prev ? { ...prev, [key]: value } : prev);
  }, []);

  if (loading) {
    return (
      <div id="loading">
        <p>Connecting to daemon…</p>
      </div>
    );
  }

  if (!config) {
    return (
      <div id="loading">
        <p>Could not reach the API server. Is <code>turnup-ui</code> running?</p>
      </div>
    );
  }

  return (
    <>
      <header id="app-header">
        <h1>TurnUp</h1>
        {isDirty && <span id="dirty-badge" className="visible">unsaved changes</span>}
        <button
          className="btn-secondary"
          onClick={handleRevert}
          disabled={!isDirty}
        >
          Revert
        </button>
        <button
          className="btn-primary"
          onClick={() => void handleSave()}
          disabled={!isDirty || saving}
        >
          {saving ? 'Saving…' : 'Save Config'}
        </button>
      </header>

      <main>
        <Connection
          config={config}
          onChange={(patch) => setConfig((prev) => prev ? { ...prev, ...patch } : prev)}
        />

        <GlobalLeds
          leds={config.leds}
          onChange={(leds) => patchConfig('leds', leds)}
        />

        <section className="card" id="knobs">
          <h2>Knobs</h2>
          <div className="cards-grid">
            {KNOB_INDICES.map((i) => (
              <KnobCard
                key={i}
                index={Number(i)}
                knob={config.knobs[i] ?? { action: 'sink_volume', target: 'default' }}
                onChange={(knob) =>
                  patchConfig('knobs', { ...config.knobs, [i]: knob })
                }
              />
            ))}
          </div>
        </section>

        <section className="card" id="buttons">
          <h2>Buttons</h2>
          <div className="cards-grid">
            {BTN_INDICES.map((i) => (
              <ButtonCard
                key={i}
                index={Number(i)}
                btn={config.buttons[i] ?? { action: 'mute_sink', target: 'default' }}
                onChange={(btn) =>
                  patchConfig('buttons', { ...config.buttons, [i]: btn })
                }
              />
            ))}
          </div>
        </section>

        <Presets
          presets={presets}
          config={config}
          onLoad={(cfg) => setConfig(cfg)}
          onRefresh={loadPresets}
          onToast={addToast}
        />
      </main>

      <ToastContainer toasts={toasts} onRemove={removeToast} />
    </>
  );
}
