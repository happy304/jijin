import { Space, Tag, Typography } from 'antd';

const { Text } = Typography;

export function AdvisorQuickFundTags({
  title,
  color,
  codes,
  onPick,
}: {
  title: string;
  color: string;
  codes: string[];
  onPick: (code: string) => void;
}) {
  if (codes.length === 0) return null;

  return (
    <Space wrap>
      <Text type="secondary">{title}</Text>
      {codes.map((code) => (
        <Tag key={`${title}-${code}`} color={color} style={{ cursor: 'pointer' }} onClick={() => onPick(code)}>
          {code}
        </Tag>
      ))}
    </Space>
  );
}
