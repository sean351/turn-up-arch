/**
 * Tests for KnobCard — running-apps dropdown feature.
 *
 * Covers:
 *  app_volume  — datalist rendered when runningApps provided
 *  app_volume  — no datalist when runningApps is empty / not passed
 *  app_volume  — picking a suggestion calls onChange with the chosen value
 *  group_volume — app-picker select rendered when runningApps provided
 *  group_volume — no app-picker when runningApps is empty / not passed
 *  group_volume — Add button is disabled until an app is selected
 *  group_volume — clicking Add appends the app to targets
 *  group_volume — clicking Add does NOT add a duplicate app
 */

import { render, screen, fireEvent } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { KnobCard } from '../components/KnobCard';
import type { KnobConfig } from '../types';

// ── helpers ───────────────────────────────────────────────────────────────────

function renderCard(
  overrides: Partial<KnobConfig> = {},
  runningApps?: string[],
  onChange = vi.fn(),
) {
  const knob: KnobConfig = {
    action: 'app_volume',
    target: 'default',
    ...overrides,
  };
  return {
    onChange,
    ...render(
      <KnobCard index={1} knob={knob} runningApps={runningApps} onChange={onChange} />,
    ),
  };
}

// ── app_volume tests ──────────────────────────────────────────────────────────

describe('KnobCard — app_volume', () => {
  it('renders a datalist with the supplied running apps', () => {
    renderCard({ action: 'app_volume' }, ['spotify', 'vlc', 'brave']);

    // An input with a list attribute is promoted to combobox by jsdom; query
    // by label text instead to avoid coupling to the ARIA role inference.
    const input = screen.getByLabelText<HTMLInputElement>('Target');
    const datalistId = input.getAttribute('list');
    expect(datalistId).toBeTruthy();

    const datalist = document.getElementById(datalistId!);
    expect(datalist).toBeInTheDocument();
    expect(datalist!.querySelectorAll('option')).toHaveLength(3);
    const values = Array.from(datalist!.querySelectorAll('option')).map(
      (o) => (o as HTMLOptionElement).value,
    );
    expect(values).toEqual(['spotify', 'vlc', 'brave']);
  });

  it('does not attach a datalist when runningApps is empty', () => {
    renderCard({ action: 'app_volume' }, []);

    const input = screen.getByLabelText<HTMLInputElement>('Target');
    expect(input.getAttribute('list')).toBeNull();
  });

  it('does not attach a datalist when runningApps is not provided', () => {
    renderCard({ action: 'app_volume' }, undefined);

    const input = screen.getByLabelText<HTMLInputElement>('Target');
    expect(input.getAttribute('list')).toBeNull();
  });

  it('calls onChange with the typed/selected value when the input changes', () => {
    const onChange = vi.fn();
    renderCard({ action: 'app_volume', target: 'default' }, ['spotify'], onChange);

    // fireEvent.change is appropriate for a controlled input: we simulate
    // the browser firing a change event with the full desired value.
    fireEvent.change(screen.getByLabelText('Target'), {
      target: { value: 'brave' },
    });

    const lastCall = onChange.mock.calls.at(-1)![0] as KnobConfig;
    expect(lastCall.target).toBe('brave');
  });
});

// ── group_volume tests ────────────────────────────────────────────────────────

describe('KnobCard — group_volume', () => {
  it('renders the app-picker select with running apps', () => {
    renderCard({ action: 'group_volume', targets: [] }, ['vlc', 'brave']);

    // The placeholder option + 2 apps
    const select = screen.getByRole<HTMLSelectElement>('combobox', {
      name: /pick a running app/i,
    });
    expect(select).toBeInTheDocument();
    const options = Array.from(select.options).slice(1); // skip placeholder
    expect(options.map((o) => o.value)).toEqual(['vlc', 'brave']);
  });

  it('does not render the app-picker when runningApps is empty', () => {
    renderCard({ action: 'group_volume', targets: [] }, []);

    expect(
      screen.queryByRole('combobox', { name: /pick a running app/i }),
    ).not.toBeInTheDocument();
  });

  it('does not render the app-picker when runningApps is not provided', () => {
    renderCard({ action: 'group_volume', targets: [] }, undefined);

    expect(
      screen.queryByRole('combobox', { name: /pick a running app/i }),
    ).not.toBeInTheDocument();
  });

  it('Add button is disabled until an app is selected from the dropdown', () => {
    renderCard({ action: 'group_volume', targets: [] }, ['spotify']);

    const addBtn = screen.getByRole('button', { name: /add/i });
    expect(addBtn).toBeDisabled();
  });

  it('Add button becomes enabled after selecting an app', async () => {
    renderCard({ action: 'group_volume', targets: [] }, ['spotify']);

    const select = screen.getByRole('combobox', { name: /pick a running app/i });
    await userEvent.selectOptions(select, 'spotify');

    expect(screen.getByRole('button', { name: /add/i })).toBeEnabled();
  });

  it('clicking Add appends the selected app to targets', async () => {
    const onChange = vi.fn();
    renderCard({ action: 'group_volume', targets: ['vlc'] }, ['spotify', 'brave'], onChange);

    const select = screen.getByRole('combobox', { name: /pick a running app/i });
    await userEvent.selectOptions(select, 'brave');
    await userEvent.click(screen.getByRole('button', { name: /add/i }));

    const lastCall = onChange.mock.calls.at(-1)![0] as KnobConfig;
    expect(lastCall.targets).toEqual(['vlc', 'brave']);
  });

  it('clicking Add does not add a duplicate app', async () => {
    const onChange = vi.fn();
    renderCard({ action: 'group_volume', targets: ['brave'] }, ['spotify', 'brave'], onChange);

    const select = screen.getByRole('combobox', { name: /pick a running app/i });
    await userEvent.selectOptions(select, 'brave');
    await userEvent.click(screen.getByRole('button', { name: /add/i }));

    const lastCall = onChange.mock.calls.at(-1);
    // onChange should not have been called at all (duplicate guard)
    expect(lastCall).toBeUndefined();
  });

  it('resets the picker select back to placeholder after a successful Add', async () => {
    renderCard({ action: 'group_volume', targets: [] }, ['spotify']);

    const select = screen.getByRole<HTMLSelectElement>('combobox', {
      name: /pick a running app/i,
    });
    await userEvent.selectOptions(select, 'spotify');
    await userEvent.click(screen.getByRole('button', { name: /add/i }));

    expect(select.value).toBe('');
  });
});
