import { hexToRgb, rgbToHex } from '../utils';

interface Props {
  id:       string;
  label:    string;
  value:    [number, number, number];
  onChange: (rgb: [number, number, number]) => void;
}

export function ColorPicker({ id, label, value, onChange }: Props) {
  const hex = rgbToHex(value);
  return (
    <div>
      <label htmlFor={id}>{label}</label>
      <div className="color-row">
        <input
          type="color"
          id={id}
          value={hex}
          onChange={(e) => onChange(hexToRgb(e.target.value))}
        />
        <span className="color-hex">{hex}</span>
      </div>
    </div>
  );
}
