/** The player's unit, shared by every screen so money reads the same on each. */

interface Props {
  unit: number;
  onUnit: (unit: number) => void;
}

export function UnitInput({ unit, onUnit }: Props) {
  return (
    <label>
      Unit
      <input
        type="number"
        min={1}
        step={5}
        inputMode="numeric"
        value={unit}
        onChange={(e) => onUnit(Math.max(1, Number(e.target.value) || 1))}
      />
    </label>
  );
}
