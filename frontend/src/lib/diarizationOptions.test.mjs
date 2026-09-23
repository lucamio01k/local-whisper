import test from 'node:test'
import assert from 'node:assert/strict'
import { diarizationRequest, speakerErrors } from './diarizationOptions.js'
import { startTranscription } from '../api.js'

const automatic = { enabled: true, speakerMode: 'auto', expectedSpeakers: '3', minSpeakers: '2', maxSpeakers: '4', start: 'auto' }
test('actual form submission: automatic, exact/manual and disabled never send contradictory constraints', async () => {
  const saved = globalThis.fetch
  const forms = []
  globalThis.fetch = async (url, options) => {
    assert.equal(url, '/api/transcribe')
    forms.push(options.body)
    return { ok: true, json: async () => ({ job_id: 'test' }) }
  }
  try {
    for (const options of [automatic, { ...automatic, speakerMode: 'exact', start: 'after' }, { ...automatic, enabled: false, minSpeakers: 'invalid' }]) {
      await startTranscription({ modelName: 'large-v3-turbo', ...diarizationRequest(options) })
    }
    assert.equal(forms[0].get('diarize'), 'true')
    assert.equal(forms[0].get('diarization_start'), 'auto')
    assert.equal(forms[0].get('expected_speakers'), null)
    assert.equal(forms[0].get('min_speakers'), '2')
    assert.equal(forms[0].get('max_speakers'), '4')
    assert.equal(forms[1].get('diarization_start'), 'after')
    assert.equal(forms[1].get('expected_speakers'), '3')
    assert.equal(forms[1].get('min_speakers'), null)
    assert.equal(forms[1].get('max_speakers'), null)
    assert.equal(forms[2].get('diarize'), 'false')
    assert.equal(forms[2].get('diarization_start'), 'off')
    for (const field of ['expected_speakers', 'min_speakers', 'max_speakers']) assert.equal(forms[2].get(field), null)
    assert.equal(automatic.minSpeakers, '2') // Building requests must not discard retained UI choices.
  } finally { globalThis.fetch = saved }
})

test('counts reject empty exact count, fractions, out-of-range values and reversed limits', () => {
  for (const count of ['', '0', '21', '2.5', '-1']) {
    assert.throws(() => diarizationRequest({ ...automatic, speakerMode: 'exact', expectedSpeakers: count }))
  }
  assert.equal(Object.keys(speakerErrors({ ...automatic, minSpeakers: '', maxSpeakers: '' })).length, 0)
  assert.ok(speakerErrors({ ...automatic, minSpeakers: '5', maxSpeakers: '2' }).maxSpeakers)
  for (const count of ['1', '20']) assert.doesNotThrow(() => diarizationRequest({ ...automatic, speakerMode: 'exact', expectedSpeakers: count }))
})
