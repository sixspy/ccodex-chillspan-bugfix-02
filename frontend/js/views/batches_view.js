export const batchesViewTemplate = `
<section class="panel">
  <h2>温控批次入库</h2>
  <p class="hint">写入温度点 JSON 数组；入库后可开超温游标与封批。</p>
  <div class="form-row">
    <div><label>仓间</label>
      <select v-model.number="batchForm.chamber_id"><option v-for="c in chambers" :key="c.id" :value="c.id">{{ c.chamber_code }}</option></select>
    </div>
    <div><label>探头</label>
      <select v-model.number="batchForm.probe_id"><option v-for="p in batchProbes" :key="p.id" :value="p.id">{{ p.probe_code }}</option></select>
    </div>
    <div><label>货载编号</label><input v-model="batchForm.cargo_ref"/></div>
    <div><label>批次编码(可空)</label><input v-model="batchForm.batch_code"/></div>
  </div>
  <label>温度点 JSON</label>
  <textarea v-model="batchForm.points_json" rows="4" placeholder="[{&quot;t&quot;:&quot;08:00&quot;,&quot;c&quot;:-19}]"></textarea>
  <div class="actions">
    <button @click="createBatch">新建批次</button>
    <button class="secondary" @click="refresh">刷新</button>
  </div>
  <table class="data-table">
    <thead><tr><th>批次</th><th>仓间</th><th>探头</th><th>状态</th><th>代</th><th>快照清单</th><th>极值</th><th></th></tr></thead>
    <tbody>
      <tr v-for="b in batches" :key="b.id" @click="selectBatch(b)">
        <td>{{ b.batch_code }}</td>
        <td>{{ b.chamber_code }}</td>
        <td>{{ b.probe_code }}</td>
        <td><span class="badge" :class="statusClass(b.status)">{{ b.status }}</span></td>
        <td>G{{ b.generation }}</td>
        <td>
          <template v-if="b.status === '已封批'">
            <span class="badge" :class="b.manifest_complete ? 'pass' : 'fail'">{{ b.manifest_complete ? "齐套(" + b.seal_snapshot_count + ")" : "清单空档" }}</span>
          </template>
          <template v-else>—</template>
        </td>
        <td>{{ fmtTemp(b.min_observed) }} / {{ fmtTemp(b.max_observed) }}</td>
        <td><button class="mini-btn" @click.stop="loadBatchDetail(b.id)">详情</button></td>
      </tr>
    </tbody>
  </table>
  <div v-if="batchDetail" class="detail-box">
    <h3>{{ batchDetail.batch.batch_code }} · {{ batchDetail.batch.cargo_ref }}</h3>
    <p>代 G{{ batchDetail.batch.generation }} · 点数 {{ batchDetail.batch.temp_points.length }} · 游标 {{ batchDetail.excursions.length }} 条 · 快照清单 {{ batchDetail.seals.length }} 行（正式 {{ batchDetail.seals.filter(s => s.status === '正式').length }} 行）</p>
    <p v-if="batchDetail.batch.status === '已封批'">
      口径：<span class="badge" :class="batchDetail.batch.manifest_complete ? 'pass' : 'fail'">{{ batchDetail.batch.manifest_complete ? "代与清单齐套" : "代已升清单空档" }}</span>
    </p>
  </div>
</section>
`;