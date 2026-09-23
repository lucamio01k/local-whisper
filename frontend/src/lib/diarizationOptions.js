// UI validation and mapping; backend API remains unchanged.
export function speakerErrors({ enabled, speakerMode, expectedSpeakers, minSpeakers, maxSpeakers }) {
  if (!enabled) return {}
  const errors = {}
  const valid = value => /^\d+$/.test(String(value)) && Number(value) >= 1 && Number(value) <= 20
  if (speakerMode === 'exact') {
    if (!valid(expectedSpeakers)) errors.expectedSpeakers = 'Inserisci un numero intero da 1 a 20.'
  } else {
    for (const [key, value] of Object.entries({ minSpeakers, maxSpeakers })) {
      if (value !== '' && !valid(value)) errors[key] = 'Inserisci un numero intero da 1 a 20.'
    }
    if (!Object.keys(errors).length && minSpeakers !== '' && maxSpeakers !== '' && Number(minSpeakers) > Number(maxSpeakers)) {
      errors.maxSpeakers = 'Il massimo deve essere maggiore o uguale al minimo.'
    }
  }
  return errors
}

export function diarizationRequest(options) {
  const { enabled, speakerMode, expectedSpeakers, minSpeakers, maxSpeakers, start } = options
  if (Object.keys(speakerErrors(options)).length) throw new Error('Controlla il numero di persone nella diarizzazione.')
  return {
    diarize: enabled,
    diarizationStart: enabled ? start : 'off',
    expectedSpeakers: enabled && speakerMode === 'exact' ? Number(expectedSpeakers) : undefined,
    minSpeakers: enabled && speakerMode === 'auto' && minSpeakers !== '' ? Number(minSpeakers) : undefined,
    maxSpeakers: enabled && speakerMode === 'auto' && maxSpeakers !== '' ? Number(maxSpeakers) : undefined,
  }
}
