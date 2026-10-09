/*
Copyright (C) 2023-2026 QuantumNous

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU Affero General Public License as
published by the Free Software Foundation, either version 3 of the
License, or (at your option) any later version.

This program is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
GNU Affero General Public License for more details.

You should have received a copy of the GNU Affero General Public License
along with this program. If not, see <https://www.gnu.org/licenses/>.

For commercial licensing, please contact support@quantumnous.com
*/
import { describe, test } from 'bun:test'
import assert from 'node:assert/strict'

import { CHANNEL_TYPE_OPTIONS } from '../../constants'
import { getChannelTypeConfig } from '../channel-type-config'
import { getChannelTypeIcon, getKeyPromptForType } from '../channel-utils'

const CHANNEL_TYPE_DOUBAO_AUDIO = 62

describe('DoubaoAudio channel', () => {
  test('registers channel metadata without a hard-coded model list', () => {
    const option = CHANNEL_TYPE_OPTIONS.find(
      (item) => item.value === CHANNEL_TYPE_DOUBAO_AUDIO
    )
    const config = getChannelTypeConfig(CHANNEL_TYPE_DOUBAO_AUDIO)

    assert.deepEqual(option, {
      value: CHANNEL_TYPE_DOUBAO_AUDIO,
      label: 'DoubaoAudio',
    })
    assert.equal(config.defaultBaseUrl, 'https://openspeech.bytedance.com')
    assert.equal(
      config.hints?.models,
      'Models configured for the Doubao Audio channel'
    )
    assert.equal(getChannelTypeIcon(CHANNEL_TYPE_DOUBAO_AUDIO), 'Doubao')
    assert.equal(
      getKeyPromptForType(CHANNEL_TYPE_DOUBAO_AUDIO),
      'Enter Doubao Voice API key'
    )
  })
})
