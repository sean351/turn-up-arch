/**
 * Tests for KnobCard — target/targets dropdown feature.
 *
 * Covers:
 *  sink_volume  — dropdown renders active sinks
 *  sink_volume  — dropdown disabled when sinks is empty / not passed
 *  sink_volume  — selecting a device calls onChange correctly
 *  source_volume — dropdown renders active sources
 *  source_volume — dropdown disabled when sources is empty / not passed
 *  app_volume   — dropdown rendered with running apps
 *  app_volume   — dropdown disabled when runningApps is empty / not passed
 *  app_volume   — selecting an option calls onChange with the chosen value
 *  group_volume — multi-select listbox rendered with running apps
 *  group_volume — listbox disabled when runningApps is empty / not passed
 *  group_volume — selecting a single app fires onChange correctly
 *  group_volume — selecting multiple apps fires onChange with all selected
 *  group_volume — existing targets are pre-selected in the listbox
 */

import { render, screen, fireEvent } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { KnobCard } from '../components/KnobCard';
import type { KnobConfig, AudioDevice } from '../types';

// ── helpers ───────────────────────────────────────────────────────────────────

const SINKS: AudioDevice[] = [
  { name: 'default',                                    description: 'Default output device' },
  { name: 'alsa_output.pci.analog-stereo',              description: 'Built-in Audio Analog Stereo', is_default: true },
  { name: 'alsa_output.usb-headphones.analog-stereo',   description: 'USB Headphones' },
];

const SOURCES: AudioDevice[] = [
  { name: 'default',                              description: 'Default input device' },
  { name: 'alsa_input.pci.analog-stereo',         description: 'Built-in Microphone', is_default: true },
];

interface RenderOptions {
  runningApps?: string[];
  sinks?:       AudioDevice[];
  sources?:     AudioDevice[];
  onChange?:    (knob: KnobConfig) => void;
}

function renderCard(
  overrides: Partial<KnobConfig> = {},
  { runningApps, sinks, sources, onChange = vi.fn() as (knob: KnobConfig) => void }: RenderOptions = {},
) {
  const knob: KnobConfig = {
    action: 'app_volume',
    target: 'default',
    ...overrides,
  };
  return {
    onChange,
    ...render(
      <KnobCard
        index={1}
        knob={knob}
        runningApps={runningApps}
        sinks={sinks}
        sources={sources}
        onChange={onChange}
      />,
    ),
  };
}

// ── sink_volume tests ─────────────────────────────────────────────────────────

describe('KnobCard — sink_volume', () => {
  it('renders a dropdown with the supplied sinks', () => {
    renderCard({ action: 'sink_volume', target: 'default' }, { sinks: SINKS });

    const select = screen.getByRole<HTMLSelectElement>('combobox', { name: /target/i });
    expect(select).toBeInTheDocument();
    const values = Array.from(select.options).map((o) => o.value);
    expect(values).toEqual(SINKS.map((s) => s.name));
  });

  it('shows device descriptions as option labels', () => {
    renderCard({ action: 'sink_volume', target: 'default' }, { sinks: SINKS });

    expect(screen.getByText(/Built-in Audio Analog Stereo/)).toBeInTheDocument();
    expect(screen.getByText(/USB Headphones/)).toBeInTheDocument();
  });

  it('marks the default device in the label', () => {
    renderCard({ action: 'sink_volume', target: 'default' }, { sinks: SINKS });

    expect(screen.getByText(/Built-in Audio Analog Stereo.*\(default\)/)).toBeInTheDocument();
  });

  it('disables the dropdown when sinks is empty', () => {
    renderCard({ action: 'sink_volume' }, { sinks: [] });

    expect(screen.getByRole('combobox', { name: /target/i })).toBeDisabled();
  });

  it('disables the dropdown when sinks is not provided', () => {
    renderCard({ action: 'sink_volume' });

    expect(screen.getByRole('combobox', { name: /target/i })).toBeDisabled();
  });

  it('calls onChange with the selected sink name', () => {
    const onChange = vi.fn();
    renderCard({ action: 'sink_volume', target: 'default' }, { sinks: SINKS, onChange });

    fireEvent.change(screen.getByRole('combobox', { name: /target/i }), {
      target: { value: 'alsa_output.usb-headphones.analog-stereo' },
    });

    const lastCall = onChange.mock.calls.at(-1)![0] as KnobConfig;
    expect(lastCall.target).toBe('alsa_output.usb-headphones.analog-stereo');
  });
});

// ── source_volume tests ───────────────────────────────────────────────────────

describe('KnobCard — source_volume', () => {
  it('renders a dropdown with the supplied sources', () => {
    renderCard({ action: 'source_volume', target: 'default' }, { sources: SOURCES });

    const select = screen.getByRole<HTMLSelectElement>('combobox', { name: /target/i });
    expect(select).toBeInTheDocument();
    const values = Array.from(select.options).map((o) => o.value);
    expect(values).toEqual(SOURCES.map((s) => s.name));
  });

  it('disables the dropdown when sources is empty', () => {
    renderCard({ action: 'source_volume' }, { sources: [] });

    expect(screen.getByRole('combobox', { name: /target/i })).toBeDisabled();
  });

  it('disables the dropdown when sources is not provided', () => {
    renderCard({ action: 'source_volume' });

    expect(screen.getByRole('combobox', { name: /target/i })).toBeDisabled();
  });

  it('calls onChange with the selected source name', () => {
    const onChange = vi.fn();
    renderCard({ action: 'source_volume', target: 'default' }, { sources: SOURCES, onChange });

    fireEvent.change(screen.getByRole('combobox', { name: /target/i }), {
      target: { value: 'alsa_input.pci.analog-stereo' },
    });

    const lastCall = onChange.mock.calls.at(-1)![0] as KnobConfig;
    expect(lastCall.target).toBe('alsa_input.pci.analog-stereo');
  });
});

// ── app_volume tests ──────────────────────────────────────────────────────────

describe('KnobCard — app_volume', () => {
  it('renders a dropdown with the supplied running apps', () => {
    renderCard({ action: 'app_volume' }, { runningApps: ['spotify', 'vlc', 'brave'] });

    const select = screen.getByRole<HTMLSelectElement>('combobox', { name: /target/i });
    expect(select).toBeInTheDocument();
    const options = Array.from(select.options).slice(1); // skip placeholder
    expect(options.map((o) => o.value)).toEqual(['spotify', 'vlc', 'brave']);
  });

  it('disables the dropdown when runningApps is empty', () => {
    renderCard({ action: 'app_volume' }, { runningApps: [] });

    expect(screen.getByRole('combobox', { name: /target/i })).toBeDisabled();
  });

  it('disables the dropdown when runningApps is not provided', () => {
    renderCard({ action: 'app_volume' });

    expect(screen.getByRole('combobox', { name: /target/i })).toBeDisabled();
  });

  it('calls onChange with the selected value when the dropdown changes', () => {
    const onChange = vi.fn();
    renderCard({ action: 'app_volume', target: 'default' }, { runningApps: ['spotify', 'brave'], onChange });

    fireEvent.change(screen.getByRole('combobox', { name: /target/i }), {
      target: { value: 'brave' },
    });

    const lastCall = onChange.mock.calls.at(-1)![0] as KnobConfig;
    expect(lastCall.target).toBe('brave');
  });
});

// ── group_volume tests ────────────────────────────────────────────────────────

describe('KnobCard — group_volume', () => {
  it('renders a multi-select listbox with running apps', () => {
    renderCard({ action: 'group_volume', targets: [] }, { runningApps: ['vlc', 'brave'] });

    const select = screen.getByRole<HTMLSelectElement>('listbox', { name: /targets/i });
    expect(select).toBeInTheDocument();
    const options = Array.from(select.options);
    expect(options.map((o) => o.value)).toEqual(['vlc', 'brave']);
  });

  it('renders the listbox (disabled) when runningApps is empty', () => {
    renderCard({ action: 'group_volume', targets: [] }, { runningApps: [] });

    expect(screen.getByRole('listbox', { name: /targets/i })).toBeDisabled();
  });

  it('renders the listbox (disabled) when runningApps is not provided', () => {
    renderCard({ action: 'group_volume', targets: [] });

    expect(screen.getByRole('listbox', { name: /targets/i })).toBeDisabled();
  });

  it('selecting a single app calls onChange with that target', async () => {
    const onChange = vi.fn();
    renderCard({ action: 'group_volume', targets: [] }, { runningApps: ['spotify', 'vlc'], onChange });

    const select = screen.getByRole('listbox', { name: /targets/i });
    await userEvent.selectOptions(select, 'spotify');

    const lastCall = onChange.mock.calls.at(-1)![0] as KnobConfig;
    expect(lastCall.targets).toEqual(['spotify']);
  });

  it('selecting multiple apps calls onChange with all selected targets', () => {
    const onChange = vi.fn();
    renderCard({ action: 'group_volume', targets: [] }, { runningApps: ['spotify', 'vlc', 'brave'], onChange });

    const select = screen.getByRole<HTMLSelectElement>('listbox', { name: /targets/i });

    // jsdom doesn't support setting selectedOptions via fireEvent, so mark
    // individual options as selected and dispatch the change event manually.
    Array.from(select.options).forEach((opt) => {
      opt.selected = opt.value === 'spotify' || opt.value === 'brave';
    });
    fireEvent.change(select);

    const lastCall = onChange.mock.calls.at(-1)![0] as KnobConfig;
    expect(lastCall.targets).toEqual(expect.arrayContaining(['spotify', 'brave']));
    expect(lastCall.targets).toHaveLength(2);
  });

  it('pre-selects options that are already in targets', () => {
    renderCard(
      { action: 'group_volume', targets: ['vlc', 'brave'] },
      { runningApps: ['spotify', 'vlc', 'brave'] },
    );

    const select = screen.getByRole<HTMLSelectElement>('listbox', { name: /targets/i });
    const selected = Array.from(select.options)
      .filter((o) => o.selected)
      .map((o) => o.value);
    expect(selected).toEqual(expect.arrayContaining(['vlc', 'brave']));
    expect(selected).toHaveLength(2);
  });
});
