import type { ThemeConfig } from 'antd';

export const themeConfig: ThemeConfig = {
  token: {
    colorPrimary: '#176bff',
    colorSuccess: '#1f9d68',
    colorWarning: '#d99614',
    colorError: '#d84a4a',
    colorInfo: '#176bff',
    colorTextBase: '#13233a',
    colorBgBase: '#f4f7fb',
    colorBgContainer: '#ffffff',
    colorBorder: '#d9e2ef',
    borderRadius: 10,
    borderRadiusLG: 18,
    borderRadiusSM: 8,
    boxShadow:
      '0 18px 45px rgba(22, 40, 72, 0.08), 0 4px 14px rgba(22, 40, 72, 0.05)',
    boxShadowSecondary: '0 10px 24px rgba(22, 40, 72, 0.08)',
    fontFamily:
      "'Microsoft YaHei UI', 'Microsoft YaHei', 'PingFang SC', 'Hiragino Sans GB', 'Segoe UI', sans-serif",
  },
  components: {
    Layout: {
      headerBg: 'rgba(255, 255, 255, 0.82)',
      siderBg: '#071426',
      bodyBg: '#f4f7fb',
    },
    Menu: {
      darkItemBg: 'transparent',
      darkItemSelectedBg: 'linear-gradient(135deg, #176bff 0%, #0fb7a5 100%)',
      darkItemColor: 'rgba(226, 238, 255, 0.72)',
      darkItemHoverBg: 'rgba(255,255,255,0.08)',
      darkItemHoverColor: '#ffffff',
      darkItemSelectedColor: '#ffffff',
      itemBorderRadius: 12,
    },
    Card: {
      borderRadiusLG: 18,
      headerBg: 'transparent',
    },
    Button: {
      borderRadius: 10,
      controlHeight: 36,
    },
    Table: {
      headerBg: '#f6f8fc',
      headerColor: '#44546a',
      rowHoverBg: '#f7fbff',
    },
    Alert: {
      borderRadiusLG: 14,
    },
  },
};
