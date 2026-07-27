// Serverless-функция (Vercel / Netlify). Прокси к api-cloud.ru.
// Токен храним ТОЛЬКО в env: FSSP_TOKEN
module.exports = async (req, res) => {
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Methods', 'GET,OPTIONS');
  if (req.method === 'OPTIONS') { res.status(200).end(); return; }

  var token = process.env.FSSP_TOKEN;
  if (!token) return res.status(500).json({ ok: false, error: 'FSSP_TOKEN не настроен на сервере' });

  var q = req.query || {};
  var lastname   = String(q.lastname || '').trim();
  var firstname  = String(q.firstname || '').trim();
  var secondname = String(q.secondname || '').trim();
  var birthdate  = String(q.birthdate || '').trim();
  var region     = String(q.region || '-1').trim();

  if (!lastname || !firstname) return res.status(400).json({ ok: false, error: 'Нужны фамилия и имя' });
  if (!/^\d{2}\.\d{2}\.\d{4}$/.test(birthdate)) return res.status(400).json({ ok: false, error: 'Дата рождения в формате дд.мм.гггг' });

  var params = new URLSearchParams({
    type: 'physical',
    lastname: lastname,
    firstname: firstname,
    birthdate: birthdate,
    region: region,
    token: token
  });
  if (secondname) params.set('secondname', secondname);
  params.set('onlyActual', '1');

  var url = 'https://api-cloud.ru/api/fssp.php?' + params.toString();

  // ФССП отвечает медленно; режем таймаутом, чтобы не висеть
  var ctrl = new AbortController();
  var timer = setTimeout(function () { ctrl.abort(); }, 25000);
  try {
    var r = await fetch(url, { signal: ctrl.signal });
    var text = await r.text();
    var data;
    try { data = JSON.parse(text); } catch (e) { data = { raw: text }; }
    return res.status(200).json({ ok: true, data: data });
  } catch (e) {
    return res.status(502).json({ ok: false, error: 'Не удалось получить ответ от ФССП', detail: String((e && e.message) || e) });
  } finally {
    clearTimeout(timer);
  }
};
