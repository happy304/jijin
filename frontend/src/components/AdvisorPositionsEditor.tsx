import { Button, Col, InputNumber, Row, Select } from 'antd';
import { DeleteOutlined, PlusOutlined } from '@ant-design/icons';

interface PositionItem {
  fund_code: string;
  market_value: number;
  shares: number;
  buy_date: string;
  cost_basis: number;
  amount?: number;
  cost?: number;
}

interface FundOption { value: string; label: string; }

interface AdvisorPositionsEditorProps {
  positions: PositionItem[];
  activeTab: string;
  selectedFundCodes?: string[];
  selectedStrategyFundCodes?: string[];
  fundOptions: FundOption[];
  onAddPosition: () => void;
  onRemovePosition: (index: number) => void;
  onUpdatePosition: (index: number, field: keyof PositionItem, value: string | number) => void;
}

export function AdvisorPositionsEditor({
  positions,
  activeTab,
  selectedFundCodes,
  selectedStrategyFundCodes,
  fundOptions,
  onAddPosition,
  onRemovePosition,
  onUpdatePosition,
}: AdvisorPositionsEditorProps) {
  return (
    <>
      {positions
        .map((pos, idx) => ({ pos, idx }))
        .filter(({ pos }) => {
          if (!pos.fund_code) return true;
          if (activeTab === 'manual') {
            if (!selectedFundCodes || selectedFundCodes.length === 0) return false;
            return selectedFundCodes.includes(pos.fund_code);
          }
          if (!selectedStrategyFundCodes || selectedStrategyFundCodes.length === 0) return false;
          return selectedStrategyFundCodes.includes(pos.fund_code);
        })
        .map(({ pos, idx }) => {
          const positionFundOptions = (activeTab === 'strategy' && selectedStrategyFundCodes?.length)
            ? fundOptions.filter((opt) => selectedStrategyFundCodes.includes(opt.value))
            : fundOptions;
          return (
            <Row key={idx} gutter={8} style={{ marginBottom: 8 }}>
              <Col span={7}><Select placeholder="选择基金" value={pos.fund_code||undefined} options={positionFundOptions} showSearch filterOption={(input,opt)=>(opt?.label??'').toLowerCase().includes(input.toLowerCase())} onChange={v=>onUpdatePosition(idx,'fund_code',v)} style={{width:'100%'}}/></Col>
              <Col span={4}><InputNumber placeholder="当前市值" min={0} step={1000} value={pos.market_value||undefined} onChange={v=>onUpdatePosition(idx,'market_value',v||0)} style={{width:'100%'}} addonAfter="元"/></Col>
              <Col span={4}><InputNumber placeholder="持有份额" min={0} step={100} value={pos.shares||undefined} onChange={v=>onUpdatePosition(idx,'shares',v||0)} style={{width:'100%'}} addonAfter="份"/></Col>
              <Col span={4}><InputNumber placeholder="持仓成本" min={0} step={1000} value={pos.cost_basis||undefined} onChange={v=>onUpdatePosition(idx,'cost_basis',v||0)} style={{width:'100%'}} addonAfter="元"/></Col>
              <Col span={5}><input type="date" value={pos.buy_date} onChange={e=>onUpdatePosition(idx,'buy_date',e.target.value)} style={{width:'100%',height:32,borderRadius:6,border:'1px solid #d9d9d9',padding:'0 8px'}}/></Col>
              <Col span={2}><Button danger icon={<DeleteOutlined/>} onClick={()=>onRemovePosition(idx)}/></Col>
            </Row>
          );
        })}
      <Button type="dashed" onClick={onAddPosition} icon={<PlusOutlined/>} style={{width:200}}>添加持仓</Button>
    </>
  );
}
