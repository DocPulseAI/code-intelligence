const express = require('express');
const app = express();
const router = express.Router();
app.use('/api', router);
router.get('/users/:id', auth, handler);
router.post('/users', handler);
module.exports = app;
