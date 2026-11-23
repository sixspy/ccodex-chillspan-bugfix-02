export const sealViewTemplate = `
<section class="panel">
  <h2>封批升代</h2>
  <p class="hint">校准有效且无开放游标时方可封批；升代与当代正式快照清单同事务落库，清单零增则整笔回滚。</p>
  <div class="form-row">
    <div><label>批次</label>
      <select v-model.number="sealForm.batch_id"><option v-for="b in sealableBatches" :key="b.id" :value="b.id">{{ b.batch_code }} G{{ b.generation }}</option></select>
    </div>
    <div><label>快照附言</label><input v-model="sealForm.manifest"/></div>
  </div>
  <div class="actions"><button @click="doSeal">执行封批</button></div>
  <table class="data-table">
    <thead><tr><th>批次</th><th>代</th><th>清单行</th><th>封批时间</th><th>附言摘要</th></tr></thead>
    <tbody>
      <tr v-for="s in seals" :key="s.id">
        <td>{{ s.batch_code }}</td>
        <td>G{{ s.batch_generation }}</td>
        <td><span class="badge" :class="s.status === '正式' ? 'pass' : 'fail'">{{ s.status }}</span></td>
        <td>{{ s.sealed_at }}</td>
        <td>{{ shortText(s.manifest) }}</td>
      </tr>
    </tbody>
  </table>
  <div class="detail-box">
    <h3>封批代 × 清单齐套</h3>
    <p class="hint">封批批次当代无正式清单行即空档（红），代与清单必须同代齐套。</p>
    <table class="data-table">
      <thead><tr><th>批次</th><th>代</th><th>正式清单行</th><th>口径</th></tr></thead>
      <tbody>
        <tr v-for="b in sealedBatches" :key="'gap-'+b.id">
          <td>{{ b.batch_code }}</td>
          <td>G{{ b.generation }}</td>
          <td>{{ b.seal_snapshot_count || 0 }}</td>
          <td><span class="badge" :class="b.manifest_complete ? 'pass' : 'fail'">{{ b.manifest_complete ? "齐套" : "清单空档" }}</span></td>
        </tr>
      </tbody>
    </table>
  </div>
</section>
`;
