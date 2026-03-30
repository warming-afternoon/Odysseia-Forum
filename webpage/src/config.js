export const GUILD_ID = '123'

export const CHANNEL_CATEGORIES = [
  {
    name: '分类1',
    channels: [
      { id: '122', name: '频道1' },
    ],
  },
  {
    name: '分类2',
    channels: [
      { id: '123', name: '频道2' },
    ],
  },
]

export const CHANNELS = {}
CHANNEL_CATEGORIES.forEach((cat) => {
  cat.channels.forEach((ch) => {
    CHANNELS[ch.id] = ch.name
  })
})

export const CLIENT_ID = '123'
export const AUTH_URL = 'http://localhost:10810/v1'
