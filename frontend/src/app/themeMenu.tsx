import { Dropdown, Header, Label } from '@heroui/react';
import { LuMonitor, LuMoon, LuSun } from 'react-icons/lu';
import { isThemeChoice, type ThemeControl } from '../lib/themeControl';

const choices = [
  ['system', 'System', LuMonitor],
  ['light', 'Light', LuSun],
  ['dark', 'Dark', LuMoon],
] as const;

/** Theme items for a documented single-selection Dropdown section. */
export function themeMenuSection({ theme, setTheme }: ThemeControl) {
  return (
    <Dropdown.Section
      key="theme"
      selectionMode="single"
      disallowEmptySelection
      selectedKeys={[theme]}
      onSelectionChange={(keys) => {
        if (keys === 'all') return;
        const [key] = keys;
        if (isThemeChoice(key)) setTheme(key);
      }}
    >
      <Header>Theme</Header>
      {choices.map(([id, label, Icon]) => (
        <Dropdown.Item key={id} id={id} textValue={label}>
          <Icon aria-hidden="true" />
          <Label>{label}</Label>
          <Dropdown.ItemIndicator />
        </Dropdown.Item>
      ))}
    </Dropdown.Section>
  );
}
