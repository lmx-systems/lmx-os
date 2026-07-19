import { StyleSheet, Text, TextInput, View } from 'react-native';
import type { TextInputProps } from 'react-native';

import { colors, radius, spacing, typography } from '../theme';

interface TextFieldProps extends TextInputProps {
  label: string;
}

export function TextField({ label, style, ...inputProps }: TextFieldProps) {
  return (
    <View style={styles.wrapper}>
      <Text style={typography.label}>{label}</Text>
      <TextInput
        placeholderTextColor={colors.textMuted}
        style={[styles.input, style]}
        {...inputProps}
      />
    </View>
  );
}

const styles = StyleSheet.create({
  wrapper: { marginBottom: spacing.lg },
  input: {
    marginTop: spacing.xs,
    borderWidth: 1,
    borderColor: colors.border,
    borderRadius: radius.md,
    paddingHorizontal: spacing.md,
    paddingVertical: spacing.md,
    fontSize: 16,
    backgroundColor: colors.surface,
    color: colors.textPrimary,
  },
});
