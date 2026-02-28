export type LedMode     = 'volume' | 'static' | 'off';
export type KnobAction  = 'sink_volume' | 'source_volume' | 'app_volume' | 'group_volume';
export type ButtonAction = 'mute_sink' | 'mute_source' | 'command';

export interface LedConfig {
  mode:       LedMode;
  low_color:  [number, number, number];
  high_color: [number, number, number];
}

export interface KnobConfig {
  action:   KnobAction;
  target?:  string;
  targets?: string[];
  led?:     Partial<LedConfig>;
}

export interface ButtonConfig {
  action: ButtonAction;
  target: string;
}

export interface Config {
  port:    string;
  baud:    number;
  leds:    LedConfig;
  knobs:   Record<string, KnobConfig>;
  buttons: Record<string, ButtonConfig>;
}

export interface ToastItem {
  id:      number;
  message: string;
  type:    'success' | 'error' | 'info';
}
